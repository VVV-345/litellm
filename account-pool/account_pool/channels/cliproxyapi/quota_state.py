"""合并主动额度、被动额度与缓存，保留刷新失败时的现有回退规则。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final

from account_pool.domain import ProviderEndpointFailure, QuotaBalance, QuotaSnapshot, QuotaWindow
from account_pool.providers.usage.contracts import ProviderQuotaRefresh


def _merge_quota_snapshots(
    previous: QuotaSnapshot,
    passive: QuotaSnapshot,
    active: QuotaSnapshot | None,
) -> QuotaSnapshot:
    active_failed: Final = active is not None and active.refresh_status == "failed"
    active_has_payload: Final = active is not None and not active_failed and _has_quota_payload(active)
    source: Final = (
        "provider_api"
        if active_has_payload
        else previous.source
        if active is None and previous.source == "provider_api" and _has_quota_payload(previous)
        else "stored_cache"
        if active_failed and _has_quota_payload(previous)
        else "cliproxyapi_cache"
        if _has_quota_payload(passive)
        else "stored_cache"
        if active is not None
        else previous.source
    )
    return QuotaSnapshot(
        observed_at=(
            (previous.observed_at if _has_quota_payload(previous) else passive.observed_at)
            if active_failed
            else active.observed_at
            if active is not None and active.observed_at is not None
            else passive.observed_at or previous.observed_at
        ),
        refresh_attempted_at=(
            active.refresh_attempted_at
            if active is not None and active.refresh_attempted_at is not None
            else previous.refresh_attempted_at
        ),
        source=source,
        plan_type=(
            active.plan_type
            if active is not None and active.plan_type is not None
            else passive.plan_type or previous.plan_type
        ),
        auth_file_plan_type=passive.auth_file_plan_type or previous.auth_file_plan_type,
        subscription_status=(
            active.subscription_status
            if active is not None and active.subscription_status is not None
            else passive.subscription_status or previous.subscription_status
        ),
        subscription_active_start=(
            active.subscription_active_start
            if active is not None and active.subscription_active_start is not None
            else passive.subscription_active_start or previous.subscription_active_start
        ),
        subscription_active_until=(
            active.subscription_active_until
            if active is not None and active.subscription_active_until is not None
            else passive.subscription_active_until or previous.subscription_active_until
        ),
        reset_credits_available=(
            active.reset_credits_available
            if active is not None and active.reset_credits_available is not None
            else passive.reset_credits_available
            if passive.reset_credits_available is not None
            else previous.reset_credits_available
        ),
        prepaid_balance=(
            active.prepaid_balance
            if active is not None and active.prepaid_balance is not None
            else passive.prepaid_balance
            if passive.prepaid_balance is not None
            else previous.prepaid_balance
        ),
        extra_usage_enabled=(
            active.extra_usage_enabled
            if active is not None and active.extra_usage_enabled is not None
            else passive.extra_usage_enabled
            if passive.extra_usage_enabled is not None
            else previous.extra_usage_enabled
        ),
        has_grok_code_access=(
            active.has_grok_code_access
            if active is not None and active.has_grok_code_access is not None
            else passive.has_grok_code_access
            if passive.has_grok_code_access is not None
            else previous.has_grok_code_access
        ),
        refresh_status=(
            active.refresh_status
            if active is not None and active.refresh_status is not None
            else previous.refresh_status
        ),
        refresh_error=(
            active.refresh_error if active is not None and active.refresh_status is not None else previous.refresh_error
        ),
        refresh_failures=() if active is None else active.refresh_failures,
        windows=_select_quota_windows(previous, passive, active),
        balances=_select_quota_balances(previous, passive, active),
    )


def _select_quota_windows(
    previous: QuotaSnapshot,
    passive: QuotaSnapshot,
    active: QuotaSnapshot | None,
) -> tuple[QuotaWindow, ...]:
    if active is not None and active.windows:
        return active.windows
    if (
        (active is None or active.refresh_status == "failed")
        and previous.source in ("provider_api", "stored_cache")
        and previous.windows
    ):
        return previous.windows
    return passive.windows or previous.windows


def _select_quota_balances(
    previous: QuotaSnapshot,
    passive: QuotaSnapshot,
    active: QuotaSnapshot | None,
) -> tuple[QuotaBalance, ...]:
    if active is not None and active.balances:
        return active.balances
    if (
        (active is None or active.refresh_status == "failed")
        and previous.source in ("provider_api", "stored_cache")
        and previous.balances
    ):
        return previous.balances
    return passive.balances or previous.balances


def _has_quota_payload(quota: QuotaSnapshot) -> bool:
    return bool(
        quota.windows
        or quota.balances
        or quota.reset_credits_available is not None
        or quota.prepaid_balance is not None
    )


def _annotate_refresh(
    refreshed: ProviderQuotaRefresh,
    failures: tuple[ProviderEndpointFailure, ...],
) -> ProviderQuotaRefresh:
    message: Final = "; ".join(failure.summary() for failure in failures)[:500] or None
    status: Final = "partial" if failures else "complete"
    return ProviderQuotaRefresh(
        quota=refreshed.quota.model_copy(
            update={
                "refresh_attempted_at": datetime.now(timezone.utc),
                "source": "provider_api",
                "refresh_status": status,
                "refresh_error": message,
                "refresh_failures": failures,
            }
        ),
        model_quotas=refreshed.model_quotas,
    )
