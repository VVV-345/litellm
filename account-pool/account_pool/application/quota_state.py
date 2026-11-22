"""计算有效额度与冷却状态，不解析供应商响应、不修改卡片或发送请求。"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from account_pool.domain import EnvironmentRecord, QuotaSnapshot
from account_pool.provider_families import provider_family_kind


def _is_feature_only_window(family: str, name: str, *, extra_usage_enabled: bool | None = None) -> bool:
    if family == "glm":
        return name == "MCP Monthly"
    if family == "anthropic_claude":
        return name in ("7 day Sonnet", "7 day Fable") or (name == "Extra usage" and extra_usage_enabled is not True)
    if family == "openai_codex":
        return name.startswith(("Code review ", "Codex Spark"))
    if family == "xai":
        return name.startswith("Tasks:") or name == "On-demand"
    return False


def routing_quota(record: EnvironmentRecord) -> QuotaSnapshot:
    family: Final = provider_family_kind(record)
    quota: Final = (
        record.display_quota
        if family in ("glm", "opencode") and record.display_quota is not None
        else record.quota
    )
    filtered: Final = tuple(
        w
        for w in quota.windows
        if not _is_feature_only_window(family, w.name, extra_usage_enabled=quota.extra_usage_enabled)
    )
    if len(filtered) == len(quota.windows):
        return quota
    return quota.model_copy(update={"windows": filtered})


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
