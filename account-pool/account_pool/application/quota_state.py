"""计算有效额度与冷却状态，不解析供应商响应、不修改卡片或发送请求。"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from account_pool.domain import EnvironmentRecord, QuotaSnapshot


def routing_quota_state(quota: QuotaSnapshot, now: datetime) -> tuple[float | None, datetime | None]:
    active: Final = tuple(w for w in quota.windows if w.resets_at is None or w.resets_at > now)
    return (
        min((w.remaining_percent for w in active), default=None),
        quota.observed_at if len(active) == len(quota.windows) else None,
    )


def effective_cooldown_until(
    record: EnvironmentRecord,
    upstream_cooldown_until: datetime | None,
    now: datetime,
) -> datetime | None:
    if upstream_cooldown_until is not None and upstream_cooldown_until > now:
        return upstream_cooldown_until
    if record.cooldown_until is not None and (record.manual_cooldown or not record.enabled):
        return record.cooldown_until
    return None
