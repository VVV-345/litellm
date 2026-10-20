"""统一计算全局设置合并后的卡片策略，供网关判定与路由投影共用。"""

from __future__ import annotations

from typing import Final

from account_pool.policies import AccountPolicy
from account_pool.settings import AccountPoolSettings

FRESHNESS_MARGIN_SECONDS: Final = 60


def minimum_snapshot_max_age(settings: AccountPoolSettings) -> int:
    return settings.quota_refresh_interval_minutes * 60 * 2 + FRESHNESS_MARGIN_SECONDS


def snapshot_max_age_floor(settings: AccountPoolSettings, *, inherit: bool) -> int:
    cadence_floor: Final = minimum_snapshot_max_age(settings)
    return max(cadence_floor, settings.quota_snapshot_max_age) if inherit else cadence_floor


def effective_policy(
    policy: AccountPolicy,
    settings: AccountPoolSettings | None,
    *,
    inherit: bool,
) -> AccountPolicy:
    if settings is None:
        return policy
    inherited: Final = _inherit(policy, settings) if inherit else policy
    max_age: Final = max(
        inherited.routing.quota_snapshot_max_age,
        snapshot_max_age_floor(settings, inherit=inherit),
    )
    return inherited.model_copy(
        update={"routing": inherited.routing.model_copy(update={"quota_snapshot_max_age": max_age})}
    )


def _inherit(policy: AccountPolicy, settings: AccountPoolSettings) -> AccountPolicy:
    return policy.model_copy(
        update={
            "routing": policy.routing.model_copy(
                update={
                    "strategy": settings.default_route,
                    "max_attempts": settings.max_attempts,
                    "quota_reserve_percent": settings.quota_reserve_percent,
                    "quota_reserve_5h_percent": settings.quota_reserve_5h_percent,
                    "quota_reserve_weekly_percent": settings.quota_reserve_weekly_percent,
                }
            ),
            "transport": policy.transport.model_copy(
                update={"request_timeout_seconds": settings.request_timeout_seconds}
            ),
        }
    )
