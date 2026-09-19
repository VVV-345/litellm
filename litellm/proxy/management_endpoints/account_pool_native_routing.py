"""将号池额度与套餐快照接入原生 Router 筛选，不发网络请求或扩大密钥范围。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, TypeVar, cast

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from litellm.constants import SESSION_DEPLOYMENT_AFFINITY_TTL_METADATA_KEY
from litellm.proxy.management_endpoints.account_pool_integration import pool_identity
from litellm.proxy.management_endpoints.account_pool_session import has_signed_history, session_identifier
from litellm.responses.utils import ResponsesAPIRequestUtils
from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import EncryptedContentAffinityCheck
from litellm.types.router import AccountPoolRoutingConfig

if TYPE_CHECKING:
    from litellm import Router


class RoutingQuotaWindow(BaseModel):
    remaining_percent: float
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
    quota_reserve_percent: float = 0
    quota_snapshot_max_age: int = 300
    plan_rank: int | None = None
    subscription_active_until: AwareDatetime | None = None


class RoutingSnapshots:
    def __init__(self) -> None:
        self.values: Mapping[str, RoutingSnapshot] = MappingProxyType({})

    def replace(self, values: Mapping[str, RoutingSnapshot]) -> None:
        self.values = MappingProxyType(dict(values))


snapshots: Final = RoutingSnapshots()
Deployment = TypeVar("Deployment", bound=Mapping[str, object])


def snapshot_for(deployment: Mapping[str, object], values: Mapping[str, RoutingSnapshot]) -> RoutingSnapshot | None:
    info: Final = deployment.get("model_info")
    if not isinstance(info, Mapping):
        return None
    card: Final = cast(Mapping[str, object], info).get("account_pool_environment_id")
    return values.get(card) if isinstance(card, str) else None


def available(snapshot: RoutingSnapshot, model: str, now: datetime) -> bool:
    target: Final = snapshot.model_aliases.get(model, model)
    cooldown: Final = snapshot.model_cooldowns.get(target)
    if cooldown is not None and cooldown > now:
        return False
    if snapshot.subscription_active_until is not None and snapshot.subscription_active_until <= now:
        return False
    quota: Final = snapshot.model_quotas.get(target, snapshot.quota)
    remaining, observed = quota.effective(now)
    if remaining is not None and remaining <= snapshot.quota_reserve_percent:
        return False
    if snapshot.quota_reserve_percent == 0:
        return True
    return (
        remaining is not None
        and observed is not None
        and (now - observed).total_seconds() <= snapshot.quota_snapshot_max_age
    )


def eligible_deployments(
    deployments: Sequence[Deployment], model: str, values: Mapping[str, RoutingSnapshot], now: datetime
) -> list[Deployment]:
    return [
        deployment
        for deployment in deployments
        if (snapshot := snapshot_for(deployment, values)) is None or available(snapshot, model, now)
    ]


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
