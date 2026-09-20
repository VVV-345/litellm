"""解析 xAI 的供应商额度响应，不处理请求和凭据。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Final

from pydantic import JsonValue

from account_pool.domain import QuotaSnapshot, QuotaWindow
from account_pool.providers.usage.common import (
    _datetime_value,
    _first_bool,
    _first_text,
    _json_value,
    _mapping,
    _number,
    _sequence,
    _text,
    _window,
)
from account_pool.providers.usage.contracts import ProviderQuotaRefresh


def parse_xai_user_id(body: str | None) -> str | None:
    root: Final = _mapping(_json_value(body))
    if root is None:
        return None
    user: Final = _mapping(root.get("user")) or root
    return next(
        (identifier for key in ("userId", "user_id", "id") if (identifier := _text(user.get(key))) is not None),
        None,
    )


def parse_xai_quota(
    weekly_body: str | None,
    monthly_body: str | None,
    observed_at: datetime,
    *,
    user_body: str | None = None,
    subscriptions_body: str | None = None,
    task_usage_body: str | None = None,
) -> ProviderQuotaRefresh | None:
    weekly_payload: Final = _json_value(weekly_body)
    monthly_payload: Final = _json_value(monthly_body)
    user_payload: Final = _json_value(user_body)
    subscriptions_payload: Final = _json_value(subscriptions_body)
    task_payload: Final = _json_value(task_usage_body)
    weekly_config: Final = _xai_config(weekly_payload)
    monthly_config: Final = _xai_config(monthly_payload)
    shared_config: Final = weekly_config or monthly_config
    period: Final = _xai_period(shared_config)
    monthly_window: Final = _xai_monthly_window(monthly_config) or _xai_monthly_window(weekly_config)
    windows: Final = tuple(
        window
        for window in (
            _xai_weekly_window(weekly_payload, shared_config),
            monthly_window,
            *_xai_product_windows(shared_config, period),
            _amount_window(
                "On-demand",
                _first_number((weekly_config, monthly_config), "onDemandUsed"),
                _first_number((weekly_config, monthly_config), "onDemandCap"),
                period,
                "cents",
            ),
            _xai_task_window("Tasks: Frequent", task_payload, "frequentUsage", "frequentLimit"),
            _xai_task_window("Tasks: Occasional", task_payload, "occasionalUsage", "occasionalLimit"),
        )
        if window is not None
    )
    subscription: Final = _xai_subscription(user_payload, subscriptions_payload, shared_config)
    plan_type: Final = _xai_plan_type(subscription, user_payload, weekly_config, monthly_config)
    status: Final = (
        None
        if subscription is None
        else _first_text((subscription,), ("status", "subscription_status", "subscriptionStatus"))
    )
    active_start: Final = _subscription_time(
        subscription,
        ("currentPeriodStart", "current_period_start", "periodStart", "period_start", "startsAt", "starts_at"),
    )
    active_until: Final = _subscription_time(
        subscription,
        (
            "currentPeriodEnd",
            "current_period_end",
            "periodEnd",
            "period_end",
            "expiresAt",
            "expires_at",
            "renewsAt",
            "renews_at",
        ),
    )
    prepaid_balance: Final = _first_number((weekly_config, monthly_config), "prepaidBalance")
    has_grok_code_access: Final = _xai_grok_code_access(user_payload)
    if (
        not windows
        and plan_type is None
        and status is None
        and prepaid_balance is None
        and has_grok_code_access is None
    ):
        return None
    return ProviderQuotaRefresh(
        quota=QuotaSnapshot(
            observed_at=observed_at,
            plan_type=plan_type,
            subscription_status=status,
            subscription_active_start=active_start,
            subscription_active_until=active_until,
            prepaid_balance=prepaid_balance,
            has_grok_code_access=has_grok_code_access,
            windows=windows,
        )
    )


def _xai_config(payload: JsonValue | None) -> Mapping[str, JsonValue] | None:
    root: Final = _mapping(payload)
    if root is None:
        return None
    return _mapping(root.get("config")) or root


def _xai_period(config: Mapping[str, JsonValue] | None) -> tuple[datetime | None, datetime | None]:
    if config is None:
        return None, None
    current: Final = _mapping(config.get("currentPeriod"))
    starts_at: Final = _datetime_value(None if current is None else current.get("start")) or _datetime_value(
        config.get("billingPeriodStart")
    )
    resets_at: Final = _datetime_value(None if current is None else current.get("end")) or _datetime_value(
        config.get("billingPeriodEnd")
    )
    return starts_at, resets_at


def _credit_bag_amounts(value: JsonValue | None) -> tuple[float | None, float | None, float | None] | None:
    items: Final = _sequence(value)
    if items is not None:
        return next((amounts for item in items if (amounts := _credit_bag_amounts(item)) is not None), None)
    item: Final = _mapping(value)
    if item is None:
        return None
    total: Final = next(
        (_number(item.get(key)) for key in ("total", "limit", "cap", "allocation", "amount") if key in item),
        None,
    )
    used: Final = next(
        (_number(item.get(key)) for key in ("used", "spent", "consumed", "usage") if key in item),
        None,
    )
    remaining: Final = next(
        (_number(item.get(key)) for key in ("remaining", "balance", "left") if key in item),
        None,
    )
    if total is None and used is None and remaining is None:
        nested: Final = item.get("bags") if item.get("bags") is not None else item.get("items")
        return _credit_bag_amounts(nested)
    resolved_used: Final = (
        used
        if used is not None
        else max(0.0, total - remaining)
        if total is not None and remaining is not None
        else None
    )
    resolved_remaining: Final = (
        remaining
        if remaining is not None
        else max(0.0, total - resolved_used)
        if total is not None and resolved_used is not None
        else None
    )
    return resolved_used, total, resolved_remaining


def _credit_sources(
    payload: JsonValue | None,
    config: Mapping[str, JsonValue] | None,
) -> tuple[JsonValue | None, ...]:
    root: Final = _mapping(payload)
    return (
        None if root is None else root.get("credits"),
        None if root is None else root.get("creditBalance"),
        None if root is None else root.get("usage"),
        None if config is None else config.get("credits"),
        None if config is None else config.get("includedCredits"),
        None if config is None else config.get("subscriptionCredits"),
        None if config is None else config.get("weeklyCredits"),
        None if config is None else config.get("sharedPool"),
    )


def _xai_weekly_window(
    payload: JsonValue | None,
    config: Mapping[str, JsonValue] | None,
) -> QuotaWindow | None:
    if config is None:
        return None
    period: Final = _xai_period(config)
    credit_amounts: Final = next(
        (
            candidate
            for source in _credit_sources(payload, config)
            if (candidate := _credit_bag_amounts(source)) is not None
        ),
        None,
    )
    used: Final = None if credit_amounts is None else credit_amounts[0]
    total: Final = None if credit_amounts is None else credit_amounts[1]
    remaining: Final = None if credit_amounts is None else credit_amounts[2]
    direct_percent: Final = _number(config.get("creditUsagePercent"))
    used_percent: Final = (
        direct_percent
        if direct_percent is not None
        else min(100.0, used / total * 100)
        if used is not None and total is not None and total > 0
        else None
    )
    if used_percent is None:
        return None
    return _window(
        "Weekly",
        used_percent,
        period[0],
        period[1],
        10080,
        used=used,
        total=total,
        remaining=remaining,
        unit="credits",
    )


def _xai_monthly_window(config: Mapping[str, JsonValue] | None) -> QuotaWindow | None:
    if config is None:
        return None
    total: Final = _number(config.get("monthlyLimit"))
    used: Final = _number(config.get("used"))
    if total is None or total <= 0 or used is None:
        return None
    included_used: Final = min(used, total)
    return _window(
        "Monthly",
        included_used / total * 100,
        _datetime_value(config.get("billingPeriodStart")),
        _datetime_value(config.get("billingPeriodEnd")),
        43200,
        used=used,
        total=total,
        remaining=max(0.0, total - used),
        unit="cents",
    )


def _xai_product_windows(
    config: Mapping[str, JsonValue] | None,
    period: tuple[datetime | None, datetime | None],
) -> tuple[QuotaWindow, ...]:
    items: Final = None if config is None else _sequence(config.get("productUsage"))
    if items is None:
        return ()
    return tuple(window for item in items if (window := _xai_product_window(item, period)) is not None)


def _xai_product_window(
    value: JsonValue,
    period: tuple[datetime | None, datetime | None],
) -> QuotaWindow | None:
    item: Final = _mapping(value)
    if item is None:
        return None
    name: Final = next(
        (text for key in ("product", "name", "productName") if (text := _text(item.get(key))) is not None),
        None,
    )
    if name is None:
        return None
    amounts: Final = _credit_bag_amounts(value)
    used: Final = None if amounts is None else amounts[0]
    total: Final = None if amounts is None else amounts[1]
    remaining: Final = None if amounts is None else amounts[2]
    direct_percent: Final = next(
        (_number(item.get(key)) for key in ("usagePercent", "usedPercent") if key in item),
        None,
    )
    used_percent: Final = (
        direct_percent
        if direct_percent is not None
        else min(100.0, used / total * 100)
        if used is not None and total is not None and total > 0
        else None
    )
    if used_percent is None:
        return None
    return _window(
        f"Product: {name}",
        used_percent,
        period[0],
        period[1],
        10080,
        used=used,
        total=total,
        remaining=remaining,
        unit="credits",
    )


def _first_number(configs: tuple[Mapping[str, JsonValue] | None, ...], key: str) -> float | None:
    return next(
        (value for config in configs if config is not None and (value := _number(config.get(key))) is not None),
        None,
    )


def _amount_window(
    name: str,
    used: float | None,
    total: float | None,
    period: tuple[datetime | None, datetime | None],
    unit: str,
) -> QuotaWindow | None:
    if used is None or total is None or total <= 0:
        return None
    return _window(
        name,
        min(100.0, used / total * 100),
        period[0],
        period[1],
        10080,
        used=used,
        total=total,
        remaining=max(0.0, total - used),
        unit=unit,
    )


def _nested_number(value: JsonValue | None, key: str) -> float | None:
    item: Final = _mapping(value)
    if item is not None:
        direct: Final = _number(item.get(key))
        return (
            direct
            if direct is not None
            else next(
                (found for child in item.values() if (found := _nested_number(child, key)) is not None),
                None,
            )
        )
    items: Final = _sequence(value)
    if items is None:
        return None
    return next((found for child in items if (found := _nested_number(child, key)) is not None), None)


def _nested_datetime(value: JsonValue | None, keys: tuple[str, ...]) -> datetime | None:
    item: Final = _mapping(value)
    if item is not None:
        direct: Final = next(
            (parsed for key in keys if (parsed := _datetime_value(item.get(key))) is not None),
            None,
        )
        return (
            direct
            if direct is not None
            else next(
                (found for child in item.values() if (found := _nested_datetime(child, keys)) is not None),
                None,
            )
        )
    items: Final = _sequence(value)
    if items is None:
        return None
    return next((found for child in items if (found := _nested_datetime(child, keys)) is not None), None)


def _xai_task_window(
    name: str,
    payload: JsonValue | None,
    used_key: str,
    limit_key: str,
) -> QuotaWindow | None:
    used: Final = _nested_number(payload, used_key)
    total: Final = _nested_number(payload, limit_key)
    reset_at: Final = _nested_datetime(payload, ("resetsAt", "resetAt", "periodEnd", "end"))
    return _amount_window(name, used, total, (None, reset_at), "tasks")


def _xai_subscription(
    user_payload: JsonValue | None,
    subscriptions_payload: JsonValue | None,
    config: Mapping[str, JsonValue] | None,
) -> Mapping[str, JsonValue] | None:
    return next(
        (
            subscription
            for candidate in (user_payload, subscriptions_payload, config)
            if (subscription := _active_subscription(candidate)) is not None
        ),
        None,
    )


def _xai_grok_code_access(user_payload: JsonValue | None) -> bool | None:
    root: Final = _mapping(user_payload)
    user: Final = None if root is None else _mapping(root.get("user")) or root
    return _first_bool((user,), ("hasGrokCodeAccess", "has_grok_code_access"))


def _active_subscription(value: JsonValue | Mapping[str, JsonValue] | None) -> Mapping[str, JsonValue] | None:
    root: Final = _mapping(value)
    user: Final = None if root is None else _mapping(root.get("user"))
    direct: Final = (
        None
        if root is None
        else _mapping(root.get("subscription")) or (None if user is None else _mapping(user.get("subscription")))
    )
    if direct is not None:
        return direct
    list_value: Final = (
        value
        if root is None
        else root.get("subscriptions")
        if root.get("subscriptions") is not None
        else None
        if user is None
        else user.get("subscriptions")
    )
    items: Final = _sequence(list_value)
    if items is None:
        return None
    subscriptions: Final = tuple(item for raw in items if (item := _mapping(raw)) is not None)
    active_statuses: Final = frozenset(("active", "trialing", "subscription_status_active", "subscriptionstatusactive"))
    return next(
        (item for item in subscriptions if (_text(item.get("status")) or "").lower() in active_statuses),
        subscriptions[0] if subscriptions else None,
    )


def _xai_plan_type(
    subscription: Mapping[str, JsonValue] | None,
    user_payload: JsonValue | None,
    weekly_config: Mapping[str, JsonValue] | None,
    monthly_config: Mapping[str, JsonValue] | None,
) -> str | None:
    user_root: Final = _mapping(user_payload)
    user: Final = None if user_root is None else _mapping(user_root.get("user")) or user_root
    explicit: Final = next(
        (
            plan
            for source, keys in (
                (weekly_config, ("subscriptionTier", "subscription_tier", "tier")),
                (monthly_config, ("subscriptionTier", "subscription_tier", "tier")),
                (subscription, ("tier", "subscriptionTier", "subscription_tier", "plan")),
                (user, ("subscriptionTier", "subscription_tier", "planType")),
            )
            if source is not None
            for key in keys
            if (plan := _text(source.get(key))) is not None
        ),
        None,
    )
    if explicit is not None:
        return explicit
    limit: Final = _first_number((monthly_config, weekly_config), "monthlyLimit")
    if limit is None:
        return None
    rounded: Final = round(limit)
    return "SuperGrok" if rounded == 15000 else "SuperGrok Heavy" if rounded == 150000 else None


def _subscription_time(
    subscription: Mapping[str, JsonValue] | None,
    keys: tuple[str, ...],
) -> datetime | None:
    if subscription is None:
        return None
    return next(
        (parsed for key in keys if (parsed := _datetime_value(subscription.get(key))) is not None),
        None,
    )
