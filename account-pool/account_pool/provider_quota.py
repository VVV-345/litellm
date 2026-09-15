"""本模块解析供应商主动额度接口，边界是不处理网络请求和凭据。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Final, TypeVar

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, JsonValue, RootModel, ValidationError, field_validator

from account_pool.domain import QuotaSnapshot, QuotaWindow
from account_pool.quota import ProviderQuotaRefresh


class _CodexWindow(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    used_percent: float | None = None
    limit_window_seconds: int | None = None
    window_minutes: int | None = None
    reset_after_seconds: int | None = None
    reset_at: int | None = None


class _CodexRateLimit(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    primary_window: _CodexWindow | None = Field(
        default=None,
        validation_alias=AliasChoices("primary_window", "primary"),
    )
    secondary_window: _CodexWindow | None = Field(
        default=None,
        validation_alias=AliasChoices("secondary_window", "secondary"),
    )


class _CodexAdditionalRateLimit(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    limit_name: str | None = None
    metered_feature: str | None = None
    rate_limit: _CodexRateLimit | None = None


class _CodexResetCredits(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    available_count: int | None = None


class _CodexUsagePayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    plan_type: str | None = None
    rate_limit: _CodexRateLimit | None = None
    code_review_rate_limit: _CodexRateLimit | None = None
    rate_limit_reset_credits: _CodexResetCredits | None = None
    additional_rate_limits: tuple[_CodexAdditionalRateLimit, ...] = ()

    @field_validator("additional_rate_limits", mode="before")
    @classmethod
    def normalize_additional_rate_limits(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        if "rate_limit" in value or "rateLimit" in value:
            return (value,)
        return tuple(
            {
                "limit_name": name,
                "rate_limit": limit.get("rate_limit", limit.get("rateLimit", limit)),
            }
            for name, limit in value.items()
            if isinstance(name, str) and isinstance(limit, Mapping)
        )


class _CodexSubscriptionPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    subscription_plan: str | None = Field(
        default=None,
        validation_alias=AliasChoices("subscription_plan", "subscriptionPlan"),
    )
    plan_type: str | None = Field(default=None, validation_alias=AliasChoices("plan_type", "planType"))
    active_start: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("active_start", "activeStart", "current_period_start", "currentPeriodStart"),
    )
    starts_at: datetime | None = Field(default=None, validation_alias=AliasChoices("starts_at", "startsAt"))
    active_until: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("active_until", "activeUntil", "current_period_end", "currentPeriodEnd"),
    )
    expires_at: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("expires_at", "expiresAt", "renews_at", "renewsAt"),
    )
    status: str | None = Field(
        default=None,
        validation_alias=AliasChoices("status", "subscription_status", "subscriptionStatus"),
    )


@dataclass(frozen=True, slots=True)
class CodexAccountInfo:
    account_id: str | None
    plan_type: str | None
    subscription_status: str | None
    subscription_active_start: datetime | None
    subscription_active_until: datetime | None
    is_default: bool


class _ClaudeUsageWindow(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    utilization: float | int | str | None = None
    resets_at: datetime | None = None


class _MoneyValue(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    val: float | int | str | None = None


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


ModelT = TypeVar("ModelT", bound=BaseModel)


class _JSONPayload(RootModel[JsonValue]):
    model_config = ConfigDict(frozen=True)


def parse_codex_account_info(
    body: str | None,
    preferred_account_id: str | None,
    observed_at: datetime,
) -> CodexAccountInfo | None:
    payload: Final = _json_value(body)
    entries: Final = _codex_account_entries(payload)
    candidates: Final = tuple(
        candidate
        for source_id, value in entries
        if (candidate := _codex_account_candidate(source_id, value, observed_at)) is not None
    )
    if not candidates:
        return None
    preferred: Final = (preferred_account_id or "").strip()
    matched: Final = next(
        (info for aliases, info in candidates if preferred and preferred in aliases),
        None,
    )
    if matched is not None:
        return matched
    default: Final = next((info for _, info in candidates if info.is_default), None)
    if default is not None:
        return default
    paid: Final = next(
        (info for _, info in candidates if info.plan_type is not None and info.plan_type.casefold() != "free"),
        None,
    )
    return paid or candidates[0][1]


def parse_codex_usage_quota(
    usage_body: str,
    observed_at: datetime,
    *,
    account_info: CodexAccountInfo | None = None,
    subscription_body: str | None = None,
    reset_credits_body: str | None = None,
) -> ProviderQuotaRefresh | None:
    try:
        usage: Final = _CodexUsagePayload.model_validate_json(usage_body)
    except ValidationError:
        return None
    subscription: Final = _parse_codex_subscription(subscription_body)
    standard: Final = _codex_rate_limit_windows("", usage.rate_limit, observed_at)
    code_review: Final = _codex_rate_limit_windows("Code review ", usage.code_review_rate_limit, observed_at)
    additional: Final = tuple(
        window
        for limit in usage.additional_rate_limits
        for window in _codex_rate_limit_windows(_codex_additional_prefix(limit), limit.rate_limit, observed_at)
    )
    detailed_reset_credits: Final = _codex_reset_credit_count(reset_credits_body, observed_at)
    reset_credits: Final = (
        detailed_reset_credits
        if detailed_reset_credits is not None
        else usage.rate_limit_reset_credits.available_count
        if usage.rate_limit_reset_credits is not None
        else None
    )
    plan_type: Final = (
        _text_value(subscription.subscription_plan if subscription is not None else None)
        or _text_value(subscription.plan_type if subscription is not None else None)
        or (None if account_info is None else account_info.plan_type)
        or _text_value(usage.plan_type)
    )
    active_start: Final = (None if subscription is None else subscription.active_start or subscription.starts_at) or (
        None if account_info is None else account_info.subscription_active_start
    )
    active_until: Final = (None if subscription is None else subscription.active_until or subscription.expires_at) or (
        None if account_info is None else account_info.subscription_active_until
    )
    status: Final = (
        _text_value(None if subscription is None else subscription.status)
        or (None if account_info is None else account_info.subscription_status)
        or _subscription_status(active_start, active_until, observed_at)
    )
    if (
        not standard
        and not code_review
        and not additional
        and plan_type is None
        and reset_credits is None
        and active_start is None
        and active_until is None
        and status is None
    ):
        return None
    return ProviderQuotaRefresh(
        quota=QuotaSnapshot(
            observed_at=observed_at,
            plan_type=plan_type,
            subscription_status=status,
            subscription_active_start=active_start,
            subscription_active_until=active_until,
            reset_credits_available=reset_credits,
            windows=(*standard, *code_review, *additional),
        )
    )


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
            _xai_task_window("Tasks: Frequent", task_payload, "frequentUsage", "frequentLimit", period),
            _xai_task_window("Tasks: Occasional", task_payload, "occasionalUsage", "occasionalLimit", period),
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
    if not windows and plan_type is None and status is None and prepaid_balance is None:
        return None
    return ProviderQuotaRefresh(
        quota=QuotaSnapshot(
            observed_at=observed_at,
            plan_type=plan_type,
            subscription_status=status,
            subscription_active_start=active_start,
            subscription_active_until=active_until,
            prepaid_balance=prepaid_balance,
            windows=windows,
        )
    )


def _parse_model(model: type[ModelT], body: str | None) -> ModelT | None:
    if body is None:
        return None
    try:
        return model.model_validate_json(body)
    except ValidationError:
        return None


def _parse_codex_subscription(body: str | None) -> _CodexSubscriptionPayload | None:
    direct: Final = _parse_model(_CodexSubscriptionPayload, body)
    if direct is not None and any(
        value is not None
        for value in (
            direct.subscription_plan,
            direct.plan_type,
            direct.active_start,
            direct.starts_at,
            direct.active_until,
            direct.expires_at,
            direct.status,
        )
    ):
        return direct
    root: Final = _mapping(_json_value(body))
    nested: Final = None if root is None else _mapping(root.get("subscription")) or _mapping(root.get("data"))
    if nested is None:
        return direct
    try:
        return _CodexSubscriptionPayload.model_validate(nested)
    except ValidationError:
        return direct


def _codex_account_entries(payload: JsonValue | None) -> tuple[tuple[str, JsonValue], ...]:
    root: Final = _mapping(payload)
    accounts_value: Final = payload if root is None else root.get("accounts")
    accounts: Final = _mapping(accounts_value)
    if accounts is not None:
        return tuple((source_id, value) for source_id, value in accounts.items())
    items: Final = _sequence(accounts_value)
    return () if items is None else tuple(("", value) for value in items)


def _codex_account_candidate(
    source_id: str,
    value: JsonValue,
    observed_at: datetime,
) -> tuple[frozenset[str], CodexAccountInfo] | None:
    record: Final = _mapping(value)
    if record is None:
        return None
    account: Final = _mapping(record.get("account")) or record
    entitlement: Final = _mapping(record.get("entitlement"))
    sources: Final = (entitlement, account, record)
    if any(source is not None and _codex_account_deactivated(source) for source in sources):
        return None
    active_until: Final = _first_datetime(
        sources,
        ("expires_at", "expiresAt", "active_until", "activeUntil", "current_period_end", "currentPeriodEnd"),
    )
    if active_until is not None and active_until <= observed_at:
        return None
    status: Final = _first_text(sources, ("subscription_status", "subscriptionStatus", "status", "state"))
    if status is not None and status.casefold() in frozenset(
        ("deactivated", "disabled", "deleted", "inactive", "suspended")
    ):
        return None
    account_id: Final = _first_text(
        (account, record),
        ("account_id", "accountId", "id", "chatgpt_account_id", "chatgptAccountId", "workspace_id", "workspaceId"),
    )
    plan_type: Final = _first_text(
        sources,
        ("subscription_plan", "subscriptionPlan", "plan_type", "planType"),
    )
    active_start: Final = _first_datetime(
        sources,
        ("active_start", "activeStart", "starts_at", "startsAt", "current_period_start", "currentPeriodStart"),
    )
    is_default: Final = _first_bool((account, record), ("is_default", "isDefault")) or False
    aliases: Final = frozenset(
        alias
        for alias in (
            _text_value(source_id),
            account_id,
            _first_text((account, record), ("organization_id", "organizationId", "workspace_id", "workspaceId")),
        )
        if alias is not None
    )
    return aliases, CodexAccountInfo(
        account_id=account_id,
        plan_type=plan_type,
        subscription_status=status or _subscription_status(active_start, active_until, observed_at),
        subscription_active_start=active_start,
        subscription_active_until=active_until,
        is_default=is_default,
    )


def _codex_account_deactivated(source: Mapping[str, JsonValue]) -> bool:
    disabled: Final = _first_bool((source,), ("deactivated", "is_deactivated", "disabled", "is_disabled"))
    timestamp: Final = _first_text((source,), ("deactivated_at", "disabled_at", "deleted_at"))
    status: Final = _first_text((source,), ("status", "state"))
    return (
        bool(disabled)
        or timestamp is not None
        or (
            status is not None
            and status.casefold() in frozenset(("deactivated", "disabled", "deleted", "inactive", "suspended"))
        )
    )


def _first_text(
    sources: tuple[Mapping[str, JsonValue] | None, ...],
    keys: tuple[str, ...],
) -> str | None:
    return next(
        (
            text
            for source in sources
            if source is not None
            for key in keys
            if (text := _text(source.get(key))) is not None
        ),
        None,
    )


def _first_datetime(
    sources: tuple[Mapping[str, JsonValue] | None, ...],
    keys: tuple[str, ...],
) -> datetime | None:
    return next(
        (
            parsed
            for source in sources
            if source is not None
            for key in keys
            if (parsed := _datetime_value(source.get(key))) is not None
        ),
        None,
    )


def _first_bool(
    sources: tuple[Mapping[str, JsonValue] | None, ...],
    keys: tuple[str, ...],
) -> bool | None:
    return next(
        (
            value
            for source in sources
            if source is not None
            for key in keys
            if isinstance((value := source.get(key)), bool)
        ),
        None,
    )


def _text_value(value: str | None) -> str | None:
    normalized: Final = "" if value is None else value.strip()
    return normalized or None


def _subscription_status(
    active_start: datetime | None,
    active_until: datetime | None,
    observed_at: datetime,
) -> str | None:
    if active_until is not None:
        return "active" if active_until > observed_at else "expired"
    if active_start is not None and active_start <= observed_at:
        return "active"
    return None


def _codex_rate_limit_windows(
    prefix: str,
    rate_limit: _CodexRateLimit | None,
    observed_at: datetime,
) -> tuple[QuotaWindow, ...]:
    if rate_limit is None:
        return ()
    candidates: Final = (
        (f"{prefix}5 hour", rate_limit.primary_window, 300),
        (f"{prefix}Weekly", rate_limit.secondary_window, 10080),
    )
    return tuple(
        window
        for name, value, fallback in candidates
        if value is not None and (window := _codex_window(name, value, observed_at, fallback)) is not None
    )


def _codex_additional_prefix(limit: _CodexAdditionalRateLimit) -> str:
    raw: Final = _text_value(limit.limit_name) or _text_value(limit.metered_feature) or "Additional"
    known: Final = {
        "codex_bengalfox": "Codex Spark",
        "codex_spark": "Codex Spark",
    }
    normalized: Final = known.get(raw.casefold()) or raw.replace("_", " ").replace("-", " ").strip().title()
    return f"{normalized} "


def _codex_reset_credit_count(body: str | None, observed_at: datetime) -> int | None:
    payload: Final = _json_value(body)
    root: Final = _mapping(payload)
    data: Final = None if root is None else _mapping(root.get("data"))
    reset_container: Final = None if root is None else _mapping(root.get("rate_limit_reset_credits"))
    explicit: Final = next(
        (
            count
            for source in (root, data, reset_container)
            if source is not None
            for key in ("available_count", "availableCount")
            if (count := _whole_number(source.get(key))) is not None
        ),
        None,
    )
    if explicit is not None:
        return explicit
    credits_value: Final = (
        payload if isinstance(payload, list) else _codex_reset_credit_list(root, data, reset_container)
    )
    credits: Final = _sequence(credits_value)
    if credits is None:
        return None
    return sum(1 for credit in credits if _codex_reset_credit_available(credit, observed_at))


def _codex_reset_credit_list(
    root: Mapping[str, JsonValue] | None,
    data: Mapping[str, JsonValue] | None,
    reset_container: Mapping[str, JsonValue] | None,
) -> JsonValue | None:
    return next(
        (
            value
            for source in (root, data, reset_container)
            if source is not None
            for key in ("credits", "rate_limit_reset_credits", "items", "data")
            if (value := source.get(key)) is not None and _sequence(value) is not None
        ),
        None,
    )


def _codex_reset_credit_available(value: JsonValue, observed_at: datetime) -> bool:
    credit: Final = _mapping(value)
    if credit is None:
        return False
    status: Final = _first_text((credit,), ("status", "state"))
    if status is not None and status.casefold() in {"redeemed", "used", "consumed", "expired"}:
        return False
    redeemed_at: Final = _first_datetime(
        (credit,),
        ("redeemed_at", "redeemedAt", "used_at", "usedAt", "consumed_at", "consumedAt"),
    )
    expires_at: Final = _first_datetime((credit,), ("expires_at", "expiresAt", "expire_at", "expireAt"))
    return redeemed_at is None and (expires_at is None or expires_at > observed_at)


def _whole_number(value: JsonValue | None) -> int | None:
    parsed: Final = _number(value)
    return int(parsed) if parsed is not None and parsed.is_integer() else None


def _codex_window(
    name: str,
    value: _CodexWindow,
    observed_at: datetime,
    fallback_minutes: int,
) -> QuotaWindow | None:
    used_percent: Final = value.used_percent
    if used_percent is None:
        return None
    resets_at: Final = (
        datetime.fromtimestamp(value.reset_at, tz=timezone.utc)
        if value.reset_at is not None and value.reset_at >= 0
        else observed_at + timedelta(seconds=value.reset_after_seconds)
        if value.reset_after_seconds is not None and value.reset_after_seconds >= 0
        else None
    )
    window_minutes: Final = (
        max(1, ceil(value.limit_window_seconds / 60))
        if value.limit_window_seconds is not None and value.limit_window_seconds > 0
        else value.window_minutes
        if value.window_minutes is not None and value.window_minutes > 0
        else fallback_minutes
    )
    starts_at: Final = None if resets_at is None else resets_at - timedelta(minutes=window_minutes)
    return _window(name, used_percent, starts_at, resets_at, window_minutes)


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


def _json_value(body: str | None) -> JsonValue | None:
    if body is None:
        return None
    try:
        return _JSONPayload.model_validate_json(body).root
    except ValidationError:
        return None


def _mapping(value: JsonValue | Mapping[str, JsonValue] | None) -> Mapping[str, JsonValue] | None:
    return value if isinstance(value, Mapping) else None


def _sequence(value: JsonValue | Mapping[str, JsonValue] | None) -> Sequence[JsonValue] | None:
    return value if isinstance(value, list) else None


def _number(value: JsonValue | _MoneyValue | None) -> float | None:
    raw: Final = value.val if isinstance(value, _MoneyValue) else value
    if isinstance(raw, Mapping):
        return _number(raw.get("val"))
    if raw is None or isinstance(raw, (bool, list, dict)):
        return None
    try:
        parsed: Final = float(raw)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _text(value: JsonValue | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized: Final = value.strip()
    return normalized or None


def _datetime_value(value: JsonValue | None) -> datetime | None:
    if value is None or isinstance(value, (bool, list, dict)):
        return None
    if isinstance(value, (int, float)):
        seconds: Final = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    raw: Final = value.strip()
    if not raw:
        return None
    try:
        numeric: Final = float(raw)
    except ValueError:
        try:
            parsed: Final = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    numeric_seconds: Final = numeric / 1000 if numeric > 10_000_000_000 else numeric
    try:
        return datetime.fromtimestamp(numeric_seconds, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


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
    fallback_period: tuple[datetime | None, datetime | None],
) -> QuotaWindow | None:
    used: Final = _nested_number(payload, used_key)
    total: Final = _nested_number(payload, limit_key)
    reset_at: Final = _nested_datetime(payload, ("resetsAt", "resetAt", "periodEnd", "end")) or fallback_period[1]
    return _amount_window(name, used, total, (fallback_period[0], reset_at), "tasks")


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


def _window(
    name: str,
    used_percent: float,
    starts_at: datetime | None,
    resets_at: datetime | None,
    fallback_minutes: int,
    *,
    used: float | None = None,
    total: float | None = None,
    remaining: float | None = None,
    unit: str | None = None,
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
        starts_at=starts_at,
        resets_at=resets_at,
        used=used,
        total=total,
        remaining=remaining,
        unit=unit,
    )
