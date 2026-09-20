"""解析 OpenAI Codex 的供应商额度响应，不处理请求和凭据。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Final

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

from account_pool.domain import QuotaSnapshot, QuotaWindow
from account_pool.providers.usage.common import (
    _first_bool,
    _first_datetime,
    _first_text,
    _json_value,
    _mapping,
    _number,
    _parse_model,
    _sequence,
    _text_value,
    _window,
)
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
        if value is None:
            return ()
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
