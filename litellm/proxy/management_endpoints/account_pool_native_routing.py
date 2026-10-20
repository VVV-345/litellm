"""将号池额度与套餐快照接入原生 Router 筛选，不发网络请求或扩大密钥范围。"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, TypeVar, cast
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from litellm._logging import verbose_router_logger
from litellm.constants import SESSION_DEPLOYMENT_AFFINITY_TTL_METADATA_KEY
from litellm.proxy.management_endpoints.account_pool_failover import exception_failure, exception_header
from litellm.proxy.management_endpoints.account_pool_integration import pool_identity
from litellm.proxy.management_endpoints.account_pool_management_models import (
    AccountPoolRoutingStatus,
    RoutingGateStatus,
    RoutingQuotaWindowStatus,
    RoutingUnavailableReason,
)
from litellm.proxy.management_endpoints.account_pool_session import has_signed_history, session_identifier
from litellm.responses.utils import ResponsesAPIRequestUtils
from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import EncryptedContentAffinityCheck
from litellm.types.router import AccountPoolRoutingConfig

if TYPE_CHECKING:
    from litellm import Router


class RoutingQuotaWindow(BaseModel):
    model_config = ConfigDict(frozen=True)
    window_minutes: int | None = Field(default=None, ge=1)
    remaining_percent: float = Field(ge=0, le=100, allow_inf_nan=False)
    resets_at: AwareDatetime | None = None


class RoutingQuota(BaseModel):
    model_config = ConfigDict(frozen=True)
    remaining_percent: float | None = Field(default=None, ge=0, le=100)
    observed_at: AwareDatetime | None = None
    windows: tuple[RoutingQuotaWindow, ...] = ()

    def effective(self, now: datetime) -> tuple[float | None, datetime | None]:
        if not self.windows:
            return self.remaining_percent, self.observed_at
        active: Final = tuple(w for w in self.windows if w.resets_at is None or w.resets_at > now)
        return (
            min((w.remaining_percent for w in active), default=None),
            self.observed_at if len(active) == len(self.windows) else None,
        )


class RoutingSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    quota: RoutingQuota = Field(default_factory=RoutingQuota)
    model_quotas: dict[str, RoutingQuota] = Field(default_factory=dict)
    model_aliases: dict[str, str] = Field(default_factory=dict)
    model_cooldowns: dict[str, AwareDatetime] = Field(default_factory=dict)
    policy_version: int = Field(default=0, ge=0)
    quota_reserve_percent: float = 0
    quota_reserve_5h_percent: int | None = Field(default=None, ge=0, le=100)
    quota_reserve_weekly_percent: int | None = Field(default=None, ge=0, le=100)
    quota_snapshot_max_age: int = 300
    supported_protocols: tuple[Literal["chat", "responses", "messages", "images"], ...] | None = None
    passthrough_enabled: bool = False
    quota_switch_project: bool = False
    quota_switch_preview_model: bool = False
    fallback_enabled: bool = False
    plan_rank: int | None = None
    subscription_active_until: AwareDatetime | None = None


class RoutingSnapshots:
    def __init__(self) -> None:
        self.values: Mapping[str, RoutingSnapshot] = MappingProxyType({})

    def replace(self, values: Mapping[str, RoutingSnapshot]) -> None:
        self.values = MappingProxyType(dict(values))


snapshots: Final = RoutingSnapshots()
Deployment = TypeVar("Deployment", bound=Mapping[str, object])


def exhausted_snapshot(
    error: Exception, deployments: Sequence[Mapping[str, object]], model: str | None = None
) -> RoutingSnapshot | None:
    """只接管已识别卡片的额度耗尽；普通速率限制仍走原生策略。"""
    identity: Final = pool_identity.get()
    if identity is None or exception_header(error, "x-account-pool-rebuild") == "disabled":
        return None
    from litellm.types.router import RouterRateLimitError, RouterRateLimitErrorBasic

    if isinstance(error, (RouterRateLimitError, RouterRateLimitErrorBasic)) and model is not None:
        scoped: Final = tuple(
            item
            for item in deployments
            if isinstance(info := item.get("model_info"), Mapping)
            and (identity.card_id is None or info.get("account_pool_environment_id") == str(identity.card_id))
        )
        known: Final = tuple(snapshot_for(item, snapshots.values) for item in scoped)
        if not known or any(snapshot is None for snapshot in known):
            return None
        depleted: Final = tuple(
            snapshot
            for snapshot in known
            if snapshot is not None
            and quota_depleted(snapshot, model, now_utc())
        )
        if len(depleted) != len(known):
            return None
        # 多卡配置不一致时保守处理：所有耗尽候选都允许，才启用模型替代。
        return RoutingSnapshot(quota_switch_preview_model=all(s.quota_switch_preview_model for s in depleted))
    failure: Final = exception_failure(error)
    if failure != "quota_exhausted" and not (
        failure is None
        and getattr(error, "status_code", None) == 429
        and re.search(r"\b(insufficient_quota|quota_exceeded|usage_limit_reached|quota_exhausted)\b", str(error))
    ):
        return None
    failed: Final = getattr(error, "failed_deployment_id", None)
    for deployment in deployments:
        info: Final = deployment.get("model_info")
        if isinstance(info, Mapping) and info.get("id") == failed and info.get("account_pool_environment_id"):
            return snapshots.values.get(str(info["account_pool_environment_id"]), RoutingSnapshot())
    return None


def unavailable_snapshot(error: Exception, deployments: Sequence[Mapping[str, object]]) -> RoutingSnapshot | None:
    reason: Final = exception_failure(error)
    replay: Final = exception_header(error, "x-account-pool-rebuild")
    if (
        pool_identity.get() is None
        or replay == "disabled"
        or reason
        not in (
            "rate_limit",
            "upstream_unavailable",
            "model_unavailable",
            "account_unavailable",
            "subscription_expired",
        )
    ):
        return None
    if reason in ("rate_limit", "upstream_unavailable") and replay != "retry-exhausted":
        return None
    failed: Final = getattr(error, "failed_deployment_id", None)
    return next(
        (
            snapshot
            for deployment in deployments
            if isinstance(info := deployment.get("model_info"), Mapping)
            and cast(  # cast-ok: Only the deployment ID is read from the checked model-info mapping.
                Mapping[str, object], info
            ).get("id")
            == failed
            and (snapshot := snapshot_for(deployment, snapshots.values)) is not None
            and snapshot.fallback_enabled
        ),
        None,
    )


def deplete_failed_deployment(
    error: Exception, deployments: Sequence[Mapping[str, object]], model: str
) -> bool:
    """网关已判定该卡不可用，立即在本地快照标记，避免后续请求等到下一次对账才绕开它。"""
    failed: Final = getattr(error, "failed_deployment_id", None)
    if not isinstance(failed, str) or not failed:
        return False
    environment: Final = next(
        (
            str(info["account_pool_environment_id"])
            for deployment in deployments
            if isinstance(info := deployment.get("model_info"), Mapping)
            and info.get("id") == failed
            and info.get("account_pool_environment_id")
        ),
        None,
    )
    current: Final = None if environment is None else snapshots.values.get(environment)
    if environment is None or current is None:
        return False
    depleted: Final = RoutingQuota(remaining_percent=0, observed_at=now_utc())
    target_model: Final = current.model_aliases.get(model, model)
    # 卡片发布分模型额度时只标记该模型，否则只能按整卡标记。
    updated: Final = (
        current.model_copy(update={"model_quotas": {**current.model_quotas, target_model: depleted}})
        if target_model in current.model_quotas
        else current.model_copy(update={"quota": depleted})
    )
    snapshots.replace({**snapshots.values, environment: updated})
    return True


def preflight_rejected(error: Exception) -> bool:
    """网关在执行前拒绝了请求，上游未被调用，因此换卡不会重放任何已产生的副作用。"""
    return exception_header(error, "x-account-pool-preflight") == "rejected"


def quota_failover_exclusions(
    deployments: Sequence[Mapping[str, object]], failed: str, previous: Sequence[str], model: str
) -> tuple[str, ...]:
    """仅保留当前密钥范围内有额度的号池部署，排除本请求已经失败的部署。"""
    identity: Final = pool_identity.get()
    eligible: Final = eligible_deployments(deployments, model, snapshots.values, now_utc())
    return tuple(
        dict.fromkeys(
            (
                *previous,
                failed,
                *(
                    str(info["id"])
                    for deployment in deployments
                    if isinstance(info := deployment.get("model_info"), Mapping)
                    and (
                        not info.get("account_pool_environment_id")
                        or deployment not in eligible
                        or identity is not None
                        and identity.card_id is not None
                        and info.get("account_pool_environment_id") != str(identity.card_id)
                    )
                ),
            )
        )
    )


def snapshot_for(deployment: Mapping[str, object], values: Mapping[str, RoutingSnapshot]) -> RoutingSnapshot | None:
    info: Final = deployment.get("model_info")
    if not isinstance(info, Mapping):
        return None
    card: Final = cast(Mapping[str, object], info).get("account_pool_environment_id")
    return values.get(card) if isinstance(card, str) else None


def window_reserve(snapshot: RoutingSnapshot, minutes: int | None) -> float:
    override: Final = (
        snapshot.quota_reserve_5h_percent if minutes == 300
        else snapshot.quota_reserve_weekly_percent if minutes == 10080 else None
    )
    return snapshot.quota_reserve_percent if override is None else override


def quota_depleted(snapshot: RoutingSnapshot, model: str, now: datetime) -> bool:
    quota: Final = snapshot.model_quotas.get(snapshot.model_aliases.get(model, model), snapshot.quota)
    if quota.windows:
        return any(
            (window.resets_at is None or window.resets_at > now)
            and window.remaining_percent <= window_reserve(snapshot, window.window_minutes)
            for window in quota.windows
        )
    return quota.remaining_percent is not None and quota.remaining_percent <= snapshot.quota_reserve_percent


QuotaFreshness: TypeAlias = Literal["missing", "fresh", "stale", "expired"]
STALE_GRACE_FACTOR: Final = 3


def quota_freshness(snapshot: RoutingSnapshot, model: str, now: datetime) -> QuotaFreshness:
    target: Final = snapshot.model_aliases.get(model, model)
    observed: Final = snapshot.model_quotas.get(target, snapshot.quota).observed_at
    if observed is None:
        return "missing"
    age: Final = (now - observed).total_seconds()
    if age <= snapshot.quota_snapshot_max_age:
        return "fresh"
    if age <= snapshot.quota_snapshot_max_age * STALE_GRACE_FACTOR:
        return "stale"
    return "expired"



def unavailable_reason(snapshot: RoutingSnapshot, model: str, now: datetime) -> RoutingUnavailableReason | None:
    target: Final = snapshot.model_aliases.get(model, model)
    cooldown: Final = snapshot.model_cooldowns.get(target)
    if cooldown is not None and cooldown > now:
        return "model_cooldown"
    if snapshot.subscription_active_until is not None and snapshot.subscription_active_until <= now:
        return "subscription_expired"
    quota: Final = snapshot.model_quotas.get(target, snapshot.quota)
    if quota_depleted(snapshot, model, now):
        return "quota_exhausted"
    reserves: Final = tuple(window_reserve(snapshot, window.window_minutes) for window in quota.windows)
    if max(reserves, default=snapshot.quota_reserve_percent) == 0:
        return None
    remaining, _ = quota.effective(now)
    if remaining is None:
        return "quota_snapshot_missing"
    freshness: Final = quota_freshness(snapshot, model, now)
    if freshness == "missing":
        return "quota_snapshot_missing"
    if freshness == "expired":
        return "quota_snapshot_expired"
    return None


def available(snapshot: RoutingSnapshot, model: str, now: datetime) -> bool:
    return unavailable_reason(snapshot, model, now) is None


def explain(card_id: UUID, snapshot: RoutingSnapshot, model: str, now: datetime) -> AccountPoolRoutingStatus:
    target: Final = snapshot.model_aliases.get(model, model)
    quota: Final = snapshot.model_quotas.get(target, snapshot.quota)
    observed: Final = quota.observed_at
    cooldown: Final = snapshot.model_cooldowns.get(target)
    freshness: Final = quota_freshness(snapshot, model, now)
    windows: Final = tuple(
        RoutingQuotaWindowStatus(
            window_minutes=window.window_minutes,
            remaining_percent=window.remaining_percent,
            threshold_percent=window_reserve(snapshot, window.window_minutes),
            resets_at=window.resets_at,
            tripped=(window.resets_at is None or window.resets_at > now)
            and window.remaining_percent <= window_reserve(snapshot, window.window_minutes),
        )
        for window in quota.windows
    )
    reason: Final = unavailable_reason(snapshot, model, now)
    return AccountPoolRoutingStatus(
        card_id=card_id,
        model=model,
        resolved_model=target,
        evaluated_at=now,
        eligible=reason is None,
        reason=reason,
        policy_version=snapshot.policy_version,
        inherits_global=snapshot.policy_version == 0,
        observed_at=observed,
        age_seconds=None if observed is None else (now - observed).total_seconds(),
        max_age_seconds=snapshot.quota_snapshot_max_age,
        stale_grace_seconds=snapshot.quota_snapshot_max_age * STALE_GRACE_FACTOR,
        quota_reserve_percent=snapshot.quota_reserve_percent,
        quota_reserve_5h_percent=snapshot.quota_reserve_5h_percent,
        quota_reserve_weekly_percent=snapshot.quota_reserve_weekly_percent,
        windows=windows,
        gates=(
            RoutingGateStatus(
                gate="model_cooldown",
                passed=cooldown is None or cooldown <= now,
                detail=None if cooldown is None else f"cooldown until {cooldown.isoformat()}",
            ),
            RoutingGateStatus(
                gate="subscription",
                passed=snapshot.subscription_active_until is None or snapshot.subscription_active_until > now,
                detail=None
                if snapshot.subscription_active_until is None
                else f"active until {snapshot.subscription_active_until.isoformat()}",
            ),
            RoutingGateStatus(gate="quota", passed=not quota_depleted(snapshot, model, now)),
            RoutingGateStatus(gate="freshness", passed=freshness in ("fresh", "stale"), detail=freshness),
        ),
    )


def protocol_available(snapshot: RoutingSnapshot) -> bool:
    identity: Final = pool_identity.get()
    return (snapshot.supported_protocols is None or identity is None or identity.protocol is None
            or identity.protocol in snapshot.supported_protocols)


def exclusion_reason(
    deployment: Deployment, model: str, values: Mapping[str, RoutingSnapshot], now: datetime
) -> str | None:
    snapshot: Final = snapshot_for(deployment, values)
    if snapshot is None:
        return None
    if not protocol_available(snapshot):
        return "protocol"
    return unavailable_reason(snapshot, model, now)


def eligible_deployments(
    deployments: Sequence[Deployment], model: str, values: Mapping[str, RoutingSnapshot], now: datetime
) -> list[Deployment]:
    eligible: Final = [
        deployment
        for deployment in deployments
        if (snapshot := snapshot_for(deployment, values)) is None
        or (protocol_available(snapshot) and available(snapshot, model, now))
    ]
    if deployments and not eligible:
        verbose_router_logger.info(
            "account pool excluded every candidate for model %s: %s",
            model,
            tuple(
                reason
                for deployment in deployments
                if (reason := exclusion_reason(deployment, model, values, now)) is not None
            ),
        )
    return eligible


def rank(snapshot: RoutingSnapshot, model: str, selection: str, now: datetime) -> tuple[float, float]:
    quota: Final = snapshot.model_quotas.get(snapshot.model_aliases.get(model, model), snapshot.quota)
    remaining_percent, observed_at = quota.effective(now)
    fresh: Final = observed_at is not None and (now - observed_at).total_seconds() <= snapshot.quota_snapshot_max_age
    remaining: Final = -remaining_percent if fresh and remaining_percent is not None else float("inf")
    if selection == "plan":
        return (-float(snapshot.plan_rank) if snapshot.plan_rank is not None else float("inf"), remaining)
    if selection == "expiry":
        return (
            snapshot.subscription_active_until.timestamp() if snapshot.subscription_active_until else float("inf"),
            remaining,
        )
    return remaining, 0.0


def preferred_deployments(
    deployments: Sequence[Deployment],
    model: str,
    config: AccountPoolRoutingConfig,
    values: Mapping[str, RoutingSnapshot],
    now: datetime,
) -> list[Deployment]:
    if config.selection == "native":
        return list(deployments)
    if config.selection == "ordered":
        positions: Final = {card: index for index, card in enumerate(config.preferred_account_ids)}
        preferred: Final = tuple(
            (
                deployment,
                positions.get(str(cast(Mapping[str, object], info).get("account_pool_environment_id")), len(positions)),
            )
            for deployment in deployments
            if isinstance((info := deployment.get("model_info")), Mapping)
            and cast(Mapping[str, object], info).get("account_pool_environment_id") is not None
        )
        best_position: Final = min((position for _, position in preferred), default=len(positions))
        return [
            deployment
            for deployment in deployments
            if not any(item is deployment for item, _ in preferred)
            or any(item is deployment and position == best_position for item, position in preferred)
        ]
    ranked: Final = tuple(
        rank(snapshot, model, config.selection, now)
        for deployment in deployments
        if (snapshot := snapshot_for(deployment, values)) is not None
    )
    best: Final = min(ranked, default=None)
    return [
        deployment
        for deployment in deployments
        if (snapshot := snapshot_for(deployment, values)) is None
        or rank(snapshot, model, config.selection, now) == best
    ]


def effective_config(default: AccountPoolRoutingConfig) -> AccountPoolRoutingConfig:
    identity: Final = pool_identity.get()
    override: Final = identity.router_settings if identity is not None else None
    return (
        override.account_pool_routing if override is not None and override.account_pool_routing is not None else default
    )


def session_metadata(
    config: AccountPoolRoutingConfig, request: Mapping[str, object] | None = None
) -> dict[str, object]:
    identity: Final = pool_identity.get()
    if not config.session_affinity or identity is None:
        return {}
    session: Final = session_identifier(dict(identity.headers), request or {})
    if session is None:
        return {}
    return {
        "session_id": session,
        "user_api_key_hash": identity.key_hash,
        SESSION_DEPLOYMENT_AFFINITY_TTL_METADATA_KEY: config.session_affinity_ttl_seconds,
        "account_pool_signed_continuation": has_signed_history(dict(request or {})),
    }


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def continuation_deployments(
    deployments: Sequence[Deployment], request: Mapping[str, object], router: Router | None = None
) -> list[Deployment]:
    previous: Final = request.get("previous_response_id")
    pinned: Final = (
        ResponsesAPIRequestUtils.get_model_id_from_response_id(previous)
        if isinstance(previous, str)
        else EncryptedContentAffinityCheck._extract_model_id_from_input(request.get("input"))  # pyright: ignore[reportPrivateUsage]  # Reuse native encrypted-content decoding to keep protocol markers compatible.
    )
    if pinned is None:
        return list(deployments)
    identity: Final = pool_identity.get()
    if identity is None:
        return list(deployments)
    pool_candidates: Final = tuple(
        item
        for item in deployments
        if isinstance(item.get("model_info"), Mapping)
        and cast(Mapping[str, object], item["model_info"]).get("account_pool_environment_id") is not None
    )
    if not pool_candidates:
        return list(deployments)
    origin: Final = router.get_deployment(model_id=pinned) if router is not None else None
    boundary: Final = (
        EncryptedContentAffinityCheck._encryption_boundary_key(origin.litellm_params)  # pyright: ignore[reportPrivateUsage]  # Reuse native boundary matching across models.
        if origin is not None and not isinstance(previous, str)
        else None
    )
    return [
        item
        for item in deployments
        if isinstance(item.get("model_info"), Mapping)
        and (
            cast(Mapping[str, object], item["model_info"]).get("id") == pinned
            or boundary is not None
            and EncryptedContentAffinityCheck._encryption_boundary_key(item.get("litellm_params")) == boundary  # pyright: ignore[reportPrivateUsage]  # Keep native boundary matching authoritative.
        )
    ]
