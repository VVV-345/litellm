"""条件写入 Cockpit 原生 Codex 池策略；不迁移授权、凭据或自动重启。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from account_pool.cockpit_admin import CockpitAdminClient, CockpitFailure, ManifestSnapshot
from account_pool.cockpit_failure import precondition, unavailable, write_outcome_unknown
from account_pool.cockpit_manifest import CockpitPolicyManifest

CockpitStrategy = Literal[
    "auto",
    "random",
    "quota_high_first",
    "quota_low_first",
    "plan_high_first",
    "plan_low_first",
    "expiry_soon_first",
    "custom",
]
STRATEGIES: Final[tuple[CockpitStrategy, ...]] = (
    "auto",
    "random",
    "quota_high_first",
    "quota_low_first",
    "plan_high_first",
    "plan_low_first",
    "expiry_soon_first",
    "custom",
)


class CockpitRoutingRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")
    priority: int = Field(default=0, ge=-10000, le=10000, strict=True)
    weight: int = Field(default=1, ge=1, le=10000, strict=True)
    is_backup: bool = Field(default=False, strict=True)
    is_preferred: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def check_role(self) -> Self:
        if self.is_backup and self.is_preferred:
            raise ValueError("An account cannot be both backup and preferred")
        return self


class CockpitPolicyUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    revision: str = Field(pattern=r'^"sha256-[0-9a-f]{64}"$')
    routing_strategy: CockpitStrategy
    custom_rules: tuple[CockpitRoutingRule, ...] = Field(max_length=256)


class CockpitPolicyView(BaseModel):
    model_config = ConfigDict(frozen=True)

    revision: str = Field(pattern=r'^"sha256-[0-9a-f]{64}"$')
    routing_strategy: str
    custom_rules: tuple[CockpitRoutingRule, ...]
    account_ids: tuple[str, ...]
    editable: bool
    blocked_reason: Literal["unsupported_account_or_rule", "unsupported_strategy"] | None
    scope: Literal["codex_pool_global"] = "codex_pool_global"
    runtime_activation: Literal["restart_required_or_unverified"] = "restart_required_or_unverified"
    supported_strategies: tuple[CockpitStrategy, ...] = STRATEGIES
    unsupported_fields: tuple[str, ...] = (
        "account_enabled",
        "candidate_scope",
        "fallback_enabled",
        "max_attempts",
        "cooldown",
        "session_affinity",
    )


@dataclass(frozen=True, slots=True)
class PolicyFailure:
    status: int
    code: Literal[
        "unavailable",
        "invalid_response",
        "unsupported_account_or_rule",
        "unsupported_strategy",
        "revision_conflict",
        "invalid_rule_accounts",
        "write_outcome_unknown",
        "write_rejected",
    ]


def _rule(raw: dict[str, JsonValue]) -> CockpitRoutingRule:
    return CockpitRoutingRule.model_validate(
        {
            "account_id": raw.get("accountId"),
            "priority": raw.get("priority", 0),
            "weight": raw.get("weight", 1),
            "is_backup": raw.get("isBackup", False),
            "is_preferred": raw.get("isPreferred", False),
        }
    )


def _view(snapshot: ManifestSnapshot) -> CockpitPolicyView | PolicyFailure:
    try:
        manifest: Final = CockpitPolicyManifest.model_validate(snapshot.data)
        rules: Final = tuple(_rule(raw) for raw in manifest.customRoutingRules or ())
    except ValidationError:
        return PolicyFailure(502, "invalid_response")
    ids: Final = tuple(account.id for account in manifest.accounts or ())
    rule_ids: Final = tuple(rule.account_id for rule in rules)
    # 全局排序只作用于 Codex 池；不声称 providerGateway 或其他凭据路径具备同等语义。
    account_blocked: Final = (
        not ids
        or len(ids) > 256
        or len(set(ids)) != len(ids)
        or len(set(rule_ids)) != len(rule_ids)
        or any(rule_id not in ids for rule_id in rule_ids)
        or any(account.authKind not in {"", "oauth"} for account in manifest.accounts or ())
    )
    reason: Final = (
        "unsupported_account_or_rule"
        if account_blocked
        else "unsupported_strategy"
        if manifest.routingStrategy not in STRATEGIES
        else None
    )
    return CockpitPolicyView(
        revision=snapshot.revision,
        routing_strategy=manifest.routingStrategy,
        custom_rules=rules,
        account_ids=ids,
        editable=reason is None,
        blocked_reason=reason,
    )


async def read_cockpit_policy(client: CockpitAdminClient | None) -> CockpitPolicyView | PolicyFailure:
    if client is None:
        return PolicyFailure(503, "unavailable")
    snapshot: Final = await client.read_manifest()
    if isinstance(snapshot, CockpitFailure):
        return PolicyFailure(503 if unavailable(snapshot) else 502, "unavailable")
    return _view(snapshot)


async def save_cockpit_policy(
    client: CockpitAdminClient | None,
    request: CockpitPolicyUpdate,
) -> CockpitPolicyView | PolicyFailure:
    if client is None:
        return PolicyFailure(503, "unavailable")
    snapshot: Final = await client.read_manifest()
    if isinstance(snapshot, CockpitFailure):
        return PolicyFailure(503, "unavailable")
    if snapshot.revision != request.revision:
        return PolicyFailure(409, "revision_conflict")
    view: Final = _view(snapshot)
    if isinstance(view, PolicyFailure):
        return view
    if view.blocked_reason is not None:
        return PolicyFailure(422, view.blocked_reason)
    ids: Final = tuple(rule.account_id for rule in request.custom_rules)
    if len(ids) != len(set(ids)) or any(account_id not in view.account_ids for account_id in ids):
        return PolicyFailure(422, "invalid_rule_accounts")
    manifest: Final = CockpitPolicyManifest.model_validate(snapshot.data)
    old_rules: Final = {
        raw.get("accountId"): raw for raw in manifest.customRoutingRules or () if isinstance(raw.get("accountId"), str)
    }
    data: Final[dict[str, JsonValue]] = {
        **snapshot.data,
        "routingStrategy": request.routing_strategy,
        "customRoutingRules": [
            {
                **old_rules.get(rule.account_id, {}),
                "accountId": rule.account_id,
                "priority": rule.priority,
                "weight": rule.weight,
                "isBackup": rule.is_backup,
                "isPreferred": rule.is_preferred,
            }
            for rule in request.custom_rules
        ],
    }
    # 单次 CAS 更新，不写第二份策略或凭据。响应丢失时不能盲目补偿覆盖并发管理员。
    receipt: Final = await client.write_manifest(data, request.revision)
    if isinstance(receipt, CockpitFailure):
        if precondition(receipt):
            return PolicyFailure(409, "revision_conflict")
        if write_outcome_unknown(receipt):
            return PolicyFailure(503, "write_outcome_unknown")
        return PolicyFailure(502, "write_rejected")
    if receipt.revision is None:
        return PolicyFailure(503, "write_outcome_unknown")
    return CockpitPolicyView(
        revision=receipt.revision,
        routing_strategy=request.routing_strategy,
        custom_rules=request.custom_rules,
        account_ids=view.account_ids,
        editable=True,
        blocked_reason=None,
    )