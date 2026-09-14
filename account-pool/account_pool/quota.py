"""本模块集中解析 CLIProxyAPI 的额度窗口和冷却截止时间。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Final, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from account_pool.domain import EnvironmentRecord, ModelQuotaSnapshot, QuotaSnapshot, QuotaWindow


class QuotaObservation(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    observed_at: datetime | None = None
    signals: Mapping[str, str] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderQuotaRefresh:
    quota: QuotaSnapshot
    model_quotas: tuple[ModelQuotaSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class AntigravityAssist:
    project_id: str | None
    plan_type: str | None


class _XAIBillingPeriod(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    type: str = ""
    start: datetime | None = None
    end: datetime | None = None


class _XAIMoney(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    val: float | int | str | None = None


_XAIMoneyValue: TypeAlias = float | int | str | _XAIMoney | None


class _XAIBillingConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    current_period: _XAIBillingPeriod | None = Field(default=None, alias="currentPeriod")
    credit_usage_percent: float | None = Field(default=None, alias="creditUsagePercent")
    monthly_limit: _XAIMoneyValue = Field(default=None, alias="monthlyLimit")
    used: _XAIMoneyValue = None
    billing_period_start: datetime | None = Field(default=None, alias="billingPeriodStart")
    billing_period_end: datetime | None = Field(default=None, alias="billingPeriodEnd")


class _XAIBillingPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    config: _XAIBillingConfig | None = None


class _AntigravityProject(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str | None = None


class _AntigravityTier(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str | None = None


class _AntigravityAssistPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    project: str | _AntigravityProject | None = Field(default=None, alias="cloudaicompanionProject")
    current_tier: _AntigravityTier | None = Field(default=None, alias="currentTier")
    paid_tier: _AntigravityTier | None = Field(default=None, alias="paidTier")


class _AntigravityQuotaInfo(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    remaining_fraction: float | None = Field(default=None, alias="remainingFraction")
    reset_time: datetime | None = Field(default=None, alias="resetTime")


class _AntigravityModelInfo(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    quota_info: _AntigravityQuotaInfo | None = Field(default=None, alias="quotaInfo")


class _AntigravityModelsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    models: Mapping[str, _AntigravityModelInfo] = Field(default_factory=dict)


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
        window for prefix in prefixes for window in _provider_windows(prefix, signals, observation.observed_at)
    )
    return QuotaSnapshot(observed_at=observation.observed_at, plan_type=plan_type, windows=windows)


def parse_xai_billing_quota(
    weekly_body: str | None,
    monthly_body: str | None,
    observed_at: datetime,
) -> ProviderQuotaRefresh | None:
    weekly: Final = _xai_payload(weekly_body)
    monthly: Final = _xai_payload(monthly_body)
    weekly_window: Final = _xai_weekly_window(weekly)
    monthly_window: Final = _xai_monthly_window(monthly)
    windows: Final = tuple(window for window in (weekly_window, monthly_window) if window is not None)
    plan_type: Final = _xai_plan_type(monthly)
    if not windows and plan_type is None:
        return None
    return ProviderQuotaRefresh(quota=QuotaSnapshot(observed_at=observed_at, plan_type=plan_type, windows=windows))


def parse_antigravity_assist(body: str | None) -> AntigravityAssist | None:
    if body is None:
        return None
    try:
        payload: Final = _AntigravityAssistPayload.model_validate_json(body)
    except ValidationError:
        return None
    project_id: Final = (
        payload.project.strip()
        if isinstance(payload.project, str) and payload.project.strip()
        else payload.project.id.strip()
        if isinstance(payload.project, _AntigravityProject)
        and payload.project.id is not None
        and payload.project.id.strip()
        else None
    )
    paid_tier: Final = payload.paid_tier.id.strip() if payload.paid_tier and payload.paid_tier.id else None
    current_tier: Final = payload.current_tier.id.strip() if payload.current_tier and payload.current_tier.id else None
    return AntigravityAssist(project_id=project_id, plan_type=paid_tier or current_tier)


def parse_antigravity_quota(
    models_body: str,
    assist: AntigravityAssist | None,
    observed_at: datetime,
) -> ProviderQuotaRefresh | None:
    try:
        payload: Final = _AntigravityModelsPayload.model_validate_json(models_body)
    except ValidationError:
        return None
    model_quotas: Final = tuple(
        model_quota
        for model, info in sorted(payload.models.items())
        if (model_quota := _antigravity_model_quota(model, info, observed_at)) is not None
    )
    plan_type: Final = None if assist is None else assist.plan_type
    if not model_quotas and plan_type is None:
        return None
    return ProviderQuotaRefresh(
        quota=QuotaSnapshot(observed_at=observed_at, plan_type=plan_type),
        model_quotas=model_quotas,
    )


def _xai_payload(body: str | None) -> _XAIBillingPayload | None:
    if body is None:
        return None
    try:
        return _XAIBillingPayload.model_validate_json(body)
    except ValidationError:
        return None


def _xai_weekly_window(payload: _XAIBillingPayload | None) -> QuotaWindow | None:
    if payload is None or payload.config is None:
        return None
    config: Final = payload.config
    period: Final = config.current_period
    if config.credit_usage_percent is None:
        return None
    return _observed_window(
        "Weekly",
        config.credit_usage_percent,
        None if period is None else period.start,
        None if period is None else period.end,
        10080,
    )


def _xai_monthly_window(payload: _XAIBillingPayload | None) -> QuotaWindow | None:
    if payload is None or payload.config is None:
        return None
    config: Final = payload.config
    limit: Final = _xai_money(config.monthly_limit)
    used: Final = _xai_money(config.used)
    if limit is None or limit <= 0 or used is None:
        return None
    return _observed_window(
        "Monthly",
        min(used, limit) / limit * 100,
        config.billing_period_start,
        config.billing_period_end,
        43200,
    )


def _xai_plan_type(payload: _XAIBillingPayload | None) -> str | None:
    if payload is None or payload.config is None:
        return None
    limit: Final = _xai_money(payload.config.monthly_limit)
    if limit is None:
        return None
    rounded: Final = round(limit)
    return "supergrok" if rounded == 15000 else "supergrok-heavy" if rounded == 150000 else None


def _xai_money(value: _XAIMoneyValue) -> float | None:
    raw: Final = value.val if isinstance(value, _XAIMoney) else value
    if raw is None or isinstance(raw, bool):
        return None
    try:
        parsed: Final = float(raw)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _observed_window(
    name: str,
    used_percent: float,
    starts_at: datetime | None,
    resets_at: datetime | None,
    fallback_minutes: int,
) -> QuotaWindow | None:
    if used_percent < 0 or used_percent > 100:
        return None
    duration: Final = (
        int((resets_at - starts_at).total_seconds() / 60)
        if starts_at is not None and resets_at is not None and resets_at > starts_at
        else fallback_minutes
    )
    return QuotaWindow(
        name=name,
        used_percent=used_percent,
        remaining_percent=100 - used_percent,
        window_minutes=max(1, duration),
        resets_at=resets_at,
    )


def _antigravity_model_quota(
    model: str,
    info: _AntigravityModelInfo,
    observed_at: datetime,
) -> ModelQuotaSnapshot | None:
    quota_info: Final = info.quota_info
    if quota_info is None or quota_info.remaining_fraction is None:
        return None
    remaining: Final = quota_info.remaining_fraction * 100
    if remaining < 0 or remaining > 100:
        return None
    resets_at: Final = quota_info.reset_time
    window_minutes: Final = (
        max(1, ceil((resets_at - observed_at).total_seconds() / 60))
        if resets_at is not None and resets_at > observed_at
        else 1
    )
    return ModelQuotaSnapshot(
        model=model,
        quota=QuotaSnapshot(
            observed_at=observed_at,
            windows=(
                QuotaWindow(
                    name="Model",
                    used_percent=100 - remaining,
                    remaining_percent=remaining,
                    window_minutes=window_minutes,
                    resets_at=resets_at,
                ),
            ),
        ),
    )


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
    return tuple(window for key in candidates if (window := _provider_window(key, signals, observed_at)) is not None)


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
    stem: Final = next(
        usage_key.removesuffix(suffix)
        for suffix in ("-utilization", "-used-percent", "-used_percent")
        if usage_key.endswith(suffix)
    )
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
