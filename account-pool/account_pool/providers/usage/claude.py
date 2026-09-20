"""解析 Claude 的供应商额度响应，不处理请求和凭据。"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, JsonValue

from account_pool.domain import QuotaSnapshot, QuotaWindow
from account_pool.providers.usage.common import (
    _first_text,
    _mapping,
    _MoneyValue,
    _number,
    _parse_model,
    _text_value,
    _window,
)
from account_pool.providers.usage.contracts import ProviderQuotaRefresh


class _ClaudeUsageWindow(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    utilization: float | int | str | None = None
    resets_at: datetime | None = None


class _ClaudeExtraUsage(_ClaudeUsageWindow):
    is_enabled: bool | None = None
    used_credits: float | int | str | _MoneyValue | None = None
    used_cents: float | int | str | _MoneyValue | None = None
    monthly_limit: float | int | str | _MoneyValue | None = None
    limit_cents: float | int | str | _MoneyValue | None = None


class _ClaudeUsageLimit(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    kind: str | None = None
    group: str | None = None
    percent: float | int | str | None = None
    utilization: float | int | str | None = None
    resets_at: datetime | None = None
    scope: JsonValue | None = None


class _ClaudeUsagePayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    five_hour: _ClaudeUsageWindow | None = None
    seven_day: _ClaudeUsageWindow | None = None
    seven_day_sonnet: _ClaudeUsageWindow | None = None
    seven_day_sonnet_4: _ClaudeUsageWindow | None = None
    seven_day_model: _ClaudeUsageWindow | None = None
    seven_day_overage_included: _ClaudeUsageWindow | None = None
    extra_usage: _ClaudeExtraUsage | None = None
    limits: tuple[_ClaudeUsageLimit, ...] = ()


class _ClaudeProfileAccount(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    has_claude_max: bool | None = None
    has_claude_pro: bool | None = None


class _ClaudeProfileOrganization(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    organization_type: str | None = None
    rate_limit_tier: str | None = None
    subscription_created_at: datetime | None = None
    subscription_status: str | None = None
    has_extra_usage_enabled: bool | None = None


class _ClaudeProfilePayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    account: _ClaudeProfileAccount | None = None
    organization: _ClaudeProfileOrganization | None = None


def parse_claude_usage_quota(
    usage_body: str | None,
    profile_body: str | None,
    observed_at: datetime,
) -> ProviderQuotaRefresh | None:
    usage: Final = _parse_model(_ClaudeUsagePayload, usage_body)
    profile: Final = _parse_model(_ClaudeProfilePayload, profile_body)
    organization: Final = None if profile is None else profile.organization
    extra_usage: Final = None if usage is None else usage.extra_usage
    windows: Final = () if usage is None else _claude_windows(usage)
    plan_type: Final = _claude_plan_type(profile)
    extra_enabled: Final = (
        extra_usage.is_enabled
        if extra_usage is not None and extra_usage.is_enabled is not None
        else None
        if organization is None
        else organization.has_extra_usage_enabled
    )
    if not windows and plan_type is None and organization is None and extra_enabled is None:
        return None
    return ProviderQuotaRefresh(
        quota=QuotaSnapshot(
            observed_at=observed_at,
            plan_type=plan_type,
            subscription_status=None if organization is None else _text_value(organization.subscription_status),
            subscription_active_start=None if organization is None else organization.subscription_created_at,
            extra_usage_enabled=extra_enabled,
            windows=windows,
        )
    )


def _claude_plan_type(profile: _ClaudeProfilePayload | None) -> str | None:
    if profile is None:
        return None
    organization: Final = profile.organization
    account: Final = profile.account
    organization_type: Final = None if organization is None else _text_value(organization.organization_type)
    known_types: Final = {
        "claude_max": "Max",
        "claude_pro": "Pro",
        "claude_enterprise": "Enterprise",
        "claude_team": "Team",
    }
    if organization_type is not None and organization_type.lower() in known_types:
        return known_types[organization_type.lower()]
    if account is not None and account.has_claude_max:
        return "Max"
    if account is not None and account.has_claude_pro:
        return "Pro"
    rate_limit_tier: Final = None if organization is None else _text_value(organization.rate_limit_tier)
    return rate_limit_tier or organization_type


def _claude_windows(payload: _ClaudeUsagePayload) -> tuple[QuotaWindow, ...]:
    sonnet: Final = payload.seven_day_sonnet or payload.seven_day_sonnet_4 or payload.seven_day_model
    fixed: Final = tuple(
        window
        for window in (
            _claude_window("5 hour", payload.five_hour, 300),
            _claude_window("7 day", payload.seven_day, 10080),
            _claude_window("7 day Sonnet", sonnet, 10080),
            _claude_window("7 day Fable", payload.seven_day_overage_included, 10080),
        )
        if window is not None
    )
    dynamic: Final = tuple(window for limit in payload.limits if (window := _claude_limit_window(limit)) is not None)
    dynamic_names: Final = frozenset(window.name for window in dynamic)
    extra: Final = _claude_extra_usage_window(payload.extra_usage)
    return (
        *dynamic,
        *(window for window in fixed if window.name not in dynamic_names),
        *((extra,) if extra is not None else ()),
    )


def _claude_limit_window(value: _ClaudeUsageLimit) -> QuotaWindow | None:
    raw_percent: Final = _number(value.percent) if value.percent is not None else _number(value.utilization)
    if raw_percent is None:
        return None
    used_percent: Final = raw_percent * 100 if 0 < raw_percent < 1 else raw_percent
    kind: Final = (_text_value(value.kind) or "").casefold()
    group: Final = (_text_value(value.group) or "").casefold()
    scope_name: Final = _claude_scope_name(value.scope)
    is_session: Final = kind == "session" or group == "session" or "5h" in kind or "5h" in group
    is_weekly: Final = kind == "weekly" or group == "weekly" or "7d" in kind or "7d" in group
    descriptor: Final = _claude_limit_descriptor(value, scope_name, is_session, is_weekly)
    return None if descriptor is None else _window(descriptor[0], used_percent, None, value.resets_at, descriptor[1])


def _claude_limit_descriptor(
    value: _ClaudeUsageLimit,
    scope_name: str | None,
    is_session: bool,
    is_weekly: bool,
) -> tuple[str, int] | None:
    if is_session:
        return "5 hour", 300
    if is_weekly:
        name: Final = (
            "7 day Sonnet"
            if scope_name is not None and "sonnet" in scope_name.casefold()
            else "7 day Fable"
            if scope_name is not None and ("fable" in scope_name.casefold() or "overage" in scope_name.casefold())
            else "7 day"
            if scope_name is None
            else f"7 day {scope_name}"
        )
        return name, 10080
    label: Final = scope_name or _text_value(value.group) or _text_value(value.kind)
    return None if label is None else (label, 1)


def _claude_scope_name(scope: JsonValue | None) -> str | None:
    root: Final = _mapping(scope)
    if root is None:
        return None
    model: Final = _mapping(root.get("model"))
    surface: Final = _mapping(root.get("surface"))
    return _first_text(
        (model, surface, root),
        ("display_name", "displayName", "name", "model", "surface"),
    )


def _claude_window(name: str, value: _ClaudeUsageWindow | None, fallback_minutes: int) -> QuotaWindow | None:
    if value is None:
        return None
    utilization: Final = _number(value.utilization)
    if utilization is None:
        return None
    return _window(name, utilization, None, value.resets_at, fallback_minutes)


def _first_number_value(values: tuple[JsonValue | _MoneyValue | None, ...]) -> float | None:
    return next((parsed for value in values if (parsed := _number(value)) is not None), None)


def _claude_extra_usage_window(value: _ClaudeExtraUsage | None) -> QuotaWindow | None:
    if value is None or value.is_enabled is False:
        return None
    used: Final = _first_number_value((value.used_credits, value.used_cents))
    total: Final = _first_number_value((value.monthly_limit, value.limit_cents))
    utilization: Final = _number(value.utilization)
    used_percent: Final = (
        utilization
        if utilization is not None
        else min(100.0, used / total * 100)
        if used is not None and total is not None and total > 0
        else None
    )
    if used_percent is None:
        return None
    return _window(
        "Extra usage",
        used_percent,
        None,
        value.resets_at,
        43200,
        used=used,
        total=total,
        remaining=None if used is None or total is None else max(0.0, total - used),
        unit="cents",
    )
