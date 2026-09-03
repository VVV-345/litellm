"""本模块集中解析 CLIProxyAPI 的额度窗口和冷却截止时间。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from account_pool.domain import EnvironmentRecord, QuotaSnapshot, QuotaWindow


class QuotaObservation(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    observed_at: datetime | None = None
    signals: Mapping[str, str] = Field(default_factory=dict)


def parse_quota(observation: QuotaObservation) -> QuotaSnapshot:
    signals: Final = {key.lower(): value for key, value in observation.signals.items()}
    plan_type: Final = signals.get("x-codex-plan-type")
    namespaces: Final = tuple(
        dict.fromkeys(
            key.removesuffix("-used-percent")
            for key in signals
            if key.startswith("x-codex-") and key.endswith("-used-percent")
        )
    )
    windows: Final = tuple(
        window
        for namespace in namespaces
        if (window := _quota_window(namespace, signals, observation.observed_at)) is not None
    )
    return QuotaSnapshot(observed_at=observation.observed_at, plan_type=plan_type, windows=windows)


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


def _quota_window(
    namespace: str,
    signals: Mapping[str, str],
    observed_at: datetime | None,
) -> QuotaWindow | None:
    used_raw: Final = signals.get(f"{namespace}-used-percent")
    minutes_raw: Final = signals.get(f"{namespace}-window-minutes")
    if used_raw is None or minutes_raw is None:
        return None
    try:
        used: Final = float(used_raw)
        minutes: Final = int(minutes_raw)
    except ValueError:
        return None
    if used < 0 or used > 100 or minutes <= 0:
        return None
    resets_at: Final = _reset_at(namespace, signals, observed_at)
    name: Final = namespace.removeprefix("x-codex-").replace("-", " ").title()
    return QuotaWindow(
        name=name,
        used_percent=used,
        remaining_percent=100 - used,
        window_minutes=minutes,
        resets_at=resets_at,
    )


def _reset_at(namespace: str, signals: Mapping[str, str], observed_at: datetime | None) -> datetime | None:
    reset_epoch: Final = signals.get(f"{namespace}-reset-at")
    if reset_epoch is not None:
        try:
            return datetime.fromtimestamp(int(reset_epoch), tz=timezone.utc)
        except (ValueError, OSError):
            return None
    reset_after: Final = signals.get(f"{namespace}-reset-after-seconds")
    if reset_after is None or observed_at is None:
        return None
    try:
        return observed_at + timedelta(seconds=int(reset_after))
    except ValueError:
        return None
