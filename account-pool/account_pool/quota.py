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


def parse_provider_quota(
    observation: QuotaObservation,
    prefixes: tuple[str, ...],
    plan_keys: tuple[str, ...] = (),
) -> QuotaSnapshot:
    signals: Final = {key.lower(): value.strip() for key, value in observation.signals.items()}
    plan_type: Final = next((signals[key] for key in plan_keys if signals.get(key)), None)
    windows: Final = tuple(
        window
        for prefix in prefixes
        for window in _provider_windows(prefix, signals, observation.observed_at)
    )
    return QuotaSnapshot(observed_at=observation.observed_at, plan_type=plan_type, windows=windows)


def _provider_windows(
    prefix: str,
    signals: Mapping[str, str],
    observed_at: datetime | None,
) -> tuple[QuotaWindow, ...]:
    candidates: Final = tuple(
        key
        for key in signals
        if key.startswith(prefix)
        and (key.endswith("-utilization") or key.endswith("-used-percent") or key.endswith("-used_percent"))
    )
    return tuple(
        window
        for key in candidates
        if (window := _provider_window(key, signals, observed_at)) is not None
    )


def _provider_window(
    usage_key: str,
    signals: Mapping[str, str],
    observed_at: datetime | None,
) -> QuotaWindow | None:
    try:
        raw_used: Final = float(signals[usage_key])
    except (KeyError, ValueError):
        return None
    used: Final = raw_used * 100 if usage_key.endswith("utilization") and raw_used <= 1 else raw_used
    if used < 0 or used > 100:
        return None
    stem: Final = usage_key.rsplit("-", 1)[0].removesuffix("_used").removesuffix("_used")
    minutes: Final = _window_minutes(stem, signals)
    if minutes is None:
        minutes = _window_length_from_name(stem)
    if minutes is None:
        return None
    reset: Final = _provider_reset_at(stem, signals, observed_at)
    label: Final = stem.rsplit("-", 1)[-1].replace("_", " ").title()
    return QuotaWindow(
        name=label,
        used_percent=used,
        remaining_percent=100 - used,
        window_minutes=minutes,
        resets_at=reset,
    )


def _window_minutes(stem: str, signals: Mapping[str, str]) -> int | None:
    for key in (f"{stem}-window-minutes", f"{stem}-window_minutes", f"{stem}-minutes"):
        raw: Final = signals.get(key)
        if raw is not None:
            try:
                value: Final = int(raw)
            except ValueError:
                return None
            return value if value > 0 else None
    return None


def _window_length_from_name(stem: str) -> int | None:
    token: Final = stem.rsplit("-", 1)[-1]
    lengths: Final = {"minute": 1, "hour": 60, "day": 1440, "week": 10080, "month": 43200}
    if token in lengths:
        return lengths[token]
    if token.endswith("h"):
        try:
            return int(token[:-1]) * 60
        except ValueError:
            return None
    if token.endswith("d"):
        try:
            return int(token[:-1]) * 1440
        except ValueError:
            return None
    return None


def _provider_reset_at(stem: str, signals: Mapping[str, str], observed_at: datetime | None) -> datetime | None:
    raw: Final = next((signals.get(f"{stem}-{suffix}") for suffix in ("reset", "resets-at", "reset-at")), None)
    if raw is None:
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    except (ValueError, OSError):
        if observed_at is None:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None


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
