"""计算额度窗口的匹配、校准、扣减和资格限制。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import groupby
from typing import Final

from account_pool.eligibility import EligibilityExclusion, EligibilityScope, EligibilitySource, activate_exclusion
from account_pool.models import (
    AccountConfig,
    Lease,
    QuotaWindowConfig,
    RuntimeQuotaKind,
    RuntimeQuotaScope,
    RuntimeQuotaWindowType,
    SettleRequest,
)
from account_pool.quota.semantics import reservation_amount as shared_reservation_amount
from account_pool.quota.semantics import window_matches_request

QUOTA_WINDOW_REASON_CODES: Final = frozenset(
    {
        "five_hour_exhausted",
        "weekly_exhausted",
        "monthly_exhausted",
        "quota_window_exhausted",
        "subscription_balance_exhausted",
    }
)


@dataclass(frozen=True, slots=True)
class QuotaUsageDelta:
    amount: Decimal
    occurred_at: float


@dataclass(frozen=True, slots=True)
class RuntimeQuotaWindow:
    config: QuotaWindowConfig
    remaining: Decimal | None
    retry_at: float | None
    usage: tuple[QuotaUsageDelta, ...] = ()
    reserved: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class QuotaReservation:
    window_id: str
    amount: Decimal
    confirms_reset: bool = False


@dataclass(frozen=True, slots=True)
class QuotaReserveSuccess:
    windows: tuple[RuntimeQuotaWindow, ...]
    reservations: tuple[QuotaReservation, ...]


@dataclass(frozen=True, slots=True)
class QuotaReserveRejected:
    reason_code: str


QuotaReserveResult = QuotaReserveSuccess | QuotaReserveRejected


def reconcile_quota_windows(
    previous: tuple[RuntimeQuotaWindow, ...],
    configured: tuple[QuotaWindowConfig, ...],
) -> tuple[RuntimeQuotaWindow, ...]:
    previous_by_id: Final = {window.config.window_id: window for window in previous}
    return tuple(
        _reconciled_window(previous=previous_by_id.get(config.window_id), configured=config) for config in configured
    )


def quota_rejection(
    windows: tuple[RuntimeQuotaWindow, ...],
    public_model: str,
    billing_route_id: str | None,
    now: float,
) -> str | None:
    return next(
        (
            normalized.config.reason_code
            for window in matching_quota_windows(windows, public_model, billing_route_id)
            for normalized in (_normalize_window(window, now),)
            if _is_exhausted(normalized) and (normalized.retry_at is None or normalized.retry_at > now)
        ),
        None,
    )


def normalize_quota_window(window: RuntimeQuotaWindow, now: float) -> RuntimeQuotaWindow:
    return _normalize_window(window, now)


def reserve_quota_capacity(
    windows: tuple[RuntimeQuotaWindow, ...],
    public_model: str,
    billing_route_id: str | None,
    estimated_tokens: int,
    now: float,
) -> QuotaReserveResult:
    matching: Final = matching_quota_windows(windows, public_model, billing_route_id)
    normalized_by_id: Final = {window.config.window_id: _normalize_window(window, now) for window in matching}
    reservations: Final = tuple(
        QuotaReservation(
            window_id=window.config.window_id,
            amount=amount,
            confirms_reset=_reset_confirmation_required(window, now),
        )
        for window in normalized_by_id.values()
        for amount in (_reservation_amount(window.config.kind, estimated_tokens),)
        if amount > 0
    )
    rejected: Final = next(
        (
            window.config.reason_code
            for window in normalized_by_id.values()
            if _reservable(window, now) <= 0
            or _reservable(window, now) < _reserved_for(window.config.window_id, reservations)
        ),
        None,
    )
    if rejected is not None:
        return QuotaReserveRejected(reason_code=rejected)
    normalized_ids: Final = frozenset(normalized_by_id)
    return QuotaReserveSuccess(
        windows=tuple(
            _reserve_window(normalized_by_id[window.config.window_id], reservations)
            if window.config.window_id in normalized_ids
            else window
            for window in windows
        ),
        reservations=reservations,
    )


def apply_quota_usage(
    windows: tuple[RuntimeQuotaWindow, ...],
    reservations: tuple[QuotaReservation, ...],
    lease: Lease,
    request: SettleRequest,
    now: float,
) -> tuple[RuntimeQuotaWindow, ...]:
    matching_ids: Final = frozenset(
        window.config.window_id
        for window in matching_quota_windows(windows, lease.public_model, lease.billing_route_id)
    )
    return tuple(
        _apply_window_usage(
            window=_release_window_reservation(window, reservations),
            request=request,
            now=now,
        )
        if window.config.window_id in matching_ids
        else _release_window_reservation(window, reservations)
        for window in windows
    )


def release_quota_capacity(
    windows: tuple[RuntimeQuotaWindow, ...],
    reservations: tuple[QuotaReservation, ...],
) -> tuple[RuntimeQuotaWindow, ...]:
    return tuple(_release_window_reservation(window, reservations) for window in windows)


def matching_quota_windows(
    windows: tuple[RuntimeQuotaWindow, ...],
    public_model: str,
    billing_route_id: str | None,
) -> tuple[RuntimeQuotaWindow, ...]:
    return tuple(window for window in windows if window_matches_request(window.config, public_model, billing_route_id))


def synchronize_quota_exclusions(
    exclusions: tuple[EligibilityExclusion, ...],
    account: AccountConfig,
    windows: tuple[RuntimeQuotaWindow, ...],
) -> tuple[EligibilityExclusion, ...]:
    retained: Final = tuple(
        exclusion
        for exclusion in exclusions
        if exclusion.account_id != account.id
        or exclusion.source != EligibilitySource.RESTRICTION
        or exclusion.reason_code not in QUOTA_WINDOW_REASON_CODES
    )
    return (*retained, *quota_window_exclusions(account=account, windows=windows))


def quota_window_exclusions(
    account: AccountConfig,
    windows: tuple[RuntimeQuotaWindow, ...],
) -> tuple[EligibilityExclusion, ...]:
    candidates: Final = tuple(
        exclusion
        for window in windows
        if _is_exhausted(window)
        for exclusion in _window_exclusions(account=account, window=window)
    )
    ordered: Final = tuple(sorted(candidates, key=_exclusion_key))
    return tuple(_merge_exclusion_group(tuple(group)) for _, group in groupby(ordered, key=_exclusion_key))


def _reconciled_window(
    previous: RuntimeQuotaWindow | None,
    configured: QuotaWindowConfig,
) -> RuntimeQuotaWindow:
    if previous is None:
        return RuntimeQuotaWindow(
            config=configured,
            remaining=configured.remaining,
            retry_at=_initial_retry_at(configured),
        )
    if configured.observed_at < previous.config.observed_at:
        return previous
    if _same_provider_snapshot(previous.config, configured):
        return RuntimeQuotaWindow(
            config=configured,
            remaining=previous.remaining,
            retry_at=previous.retry_at,
            usage=previous.usage,
            reserved=previous.reserved,
        )
    retained_usage: Final = tuple(delta for delta in previous.usage if delta.occurred_at > configured.observed_at)
    calibrated: Final = RuntimeQuotaWindow(
        config=configured,
        remaining=configured.remaining,
        retry_at=_initial_retry_at(configured),
        usage=retained_usage,
        reserved=previous.reserved,
    )
    return _reapply_usage(calibrated, retained_usage)


def _same_provider_snapshot(previous: QuotaWindowConfig, configured: QuotaWindowConfig) -> bool:
    return previous == configured


def _initial_retry_at(config: QuotaWindowConfig) -> float | None:
    if config.reset_at is not None:
        return config.reset_at
    if config.window_type == RuntimeQuotaWindowType.FIXED and config.duration_seconds is not None:
        return config.observed_at + config.duration_seconds
    if config.window_type == RuntimeQuotaWindowType.ROLLING and config.duration_seconds is not None:
        return config.observed_at + config.duration_seconds
    return None


def _apply_window_usage(
    window: RuntimeQuotaWindow,
    request: SettleRequest,
    now: float,
) -> RuntimeQuotaWindow:
    active: Final = _normalize_window(window, now, confirm_reset=request.success)
    consumption: Final = _consumption(active.config.kind, request)
    if consumption is None or active.remaining is None:
        return active
    usage: Final = (*active.usage, QuotaUsageDelta(amount=consumption, occurred_at=now))
    updated: Final = RuntimeQuotaWindow(
        config=active.config,
        remaining=max(Decimal("0"), active.remaining - consumption),
        retry_at=active.retry_at,
        usage=usage,
    )
    return _with_recovery(updated, now)


def _reapply_usage(
    window: RuntimeQuotaWindow,
    usage: tuple[QuotaUsageDelta, ...],
) -> RuntimeQuotaWindow:
    if window.remaining is None:
        return window
    consumed: Final = sum((delta.amount for delta in usage), start=Decimal("0"))
    return RuntimeQuotaWindow(
        config=window.config,
        remaining=max(Decimal("0"), window.remaining - consumed),
        retry_at=window.retry_at,
        usage=usage,
        reserved=window.reserved,
    )


def _normalize_window(
    window: RuntimeQuotaWindow,
    now: float,
    confirm_reset: bool = False,
) -> RuntimeQuotaWindow:
    if window.config.window_type == RuntimeQuotaWindowType.ROLLING:
        return _normalize_rolling_window(window, now)
    if not confirm_reset or window.retry_at is None or window.retry_at > now or window.config.limit is None:
        return window
    return RuntimeQuotaWindow(
        config=window.config,
        remaining=window.config.limit,
        retry_at=_next_fixed_retry_at(window, now),
    )


def _next_fixed_retry_at(window: RuntimeQuotaWindow, now: float) -> float | None:
    duration: Final = window.config.duration_seconds
    retry_at: Final = window.retry_at
    if duration is None or retry_at is None:
        return None
    elapsed_periods: Final = int((now - retry_at) // duration) + 1
    return retry_at + elapsed_periods * duration


def _normalize_rolling_window(window: RuntimeQuotaWindow, now: float) -> RuntimeQuotaWindow:
    duration: Final = window.config.duration_seconds
    if duration is None:
        return window
    retained: Final = tuple(delta for delta in window.usage if delta.occurred_at + duration > now)
    baseline_active: Final = window.config.observed_at + duration > now
    baseline: Final = window.config.remaining if baseline_active else window.config.limit
    remaining: Final = (
        None
        if baseline is None
        else max(Decimal("0"), baseline - sum((delta.amount for delta in retained), start=Decimal("0")))
    )
    normalized: Final = RuntimeQuotaWindow(
        config=window.config,
        remaining=remaining,
        retry_at=window.retry_at,
        usage=retained,
        reserved=window.reserved,
    )
    return _with_recovery(normalized, now)


def _with_recovery(window: RuntimeQuotaWindow, now: float) -> RuntimeQuotaWindow:
    if window.config.window_type != RuntimeQuotaWindowType.ROLLING:
        return window
    retry_at: Final = _rolling_recovery_at(window, now) if _is_exhausted(window) else None
    return RuntimeQuotaWindow(
        config=window.config,
        remaining=window.remaining,
        retry_at=retry_at,
        usage=window.usage,
        reserved=window.reserved,
    )


def _rolling_recovery_at(window: RuntimeQuotaWindow, now: float) -> float | None:
    duration: Final = window.config.duration_seconds
    if duration is None:
        return None
    boundaries: Final = tuple(
        sorted(
            {
                window.config.observed_at + duration,
                *(delta.occurred_at + duration for delta in window.usage),
            }
        )
    )
    return next(
        (boundary for boundary in boundaries if boundary > now and not _is_exhausted(_remaining_at(window, boundary))),
        None,
    )


def _remaining_at(window: RuntimeQuotaWindow, now: float) -> RuntimeQuotaWindow:
    duration: Final = window.config.duration_seconds
    if duration is None:
        return window
    retained: Final = tuple(delta for delta in window.usage if delta.occurred_at + duration > now)
    baseline: Final = window.config.remaining if window.config.observed_at + duration > now else window.config.limit
    remaining: Final = (
        None
        if baseline is None
        else max(Decimal("0"), baseline - sum((delta.amount for delta in retained), start=Decimal("0")))
    )
    return RuntimeQuotaWindow(
        config=window.config,
        remaining=remaining,
        retry_at=None,
        usage=retained,
        reserved=window.reserved,
    )


def _reservation_amount(kind: RuntimeQuotaKind, estimated_tokens: int) -> Decimal:
    return shared_reservation_amount(kind, estimated_tokens)


def _available(window: RuntimeQuotaWindow) -> Decimal:
    return (
        Decimal("Infinity")
        if window.remaining is None
        else max(Decimal("0"), window.remaining - window.config.safety_reserve - window.reserved)
    )


def _reservable(window: RuntimeQuotaWindow, now: float) -> Decimal:
    if _reset_confirmation_required(window, now) and window.config.limit is not None:
        return max(Decimal("0"), window.config.limit - window.config.safety_reserve - window.reserved)
    return _available(window)


def _reset_confirmation_required(window: RuntimeQuotaWindow, now: float) -> bool:
    return (
        window.config.window_type != RuntimeQuotaWindowType.ROLLING
        and window.retry_at is not None
        and window.retry_at <= now
        and window.config.limit is not None
        and _is_exhausted(window)
    )


def _reserved_for(window_id: str, reservations: tuple[QuotaReservation, ...]) -> Decimal:
    return sum(
        (reservation.amount for reservation in reservations if reservation.window_id == window_id),
        start=Decimal("0"),
    )


def _reserve_window(
    window: RuntimeQuotaWindow,
    reservations: tuple[QuotaReservation, ...],
) -> RuntimeQuotaWindow:
    return RuntimeQuotaWindow(
        config=window.config,
        remaining=window.remaining,
        retry_at=window.retry_at,
        usage=window.usage,
        reserved=window.reserved + _reserved_for(window.config.window_id, reservations),
    )


def _release_window_reservation(
    window: RuntimeQuotaWindow,
    reservations: tuple[QuotaReservation, ...],
) -> RuntimeQuotaWindow:
    released: Final = _reserved_for(window.config.window_id, reservations)
    if released == 0:
        return window
    return RuntimeQuotaWindow(
        config=window.config,
        remaining=window.remaining,
        retry_at=window.retry_at,
        usage=window.usage,
        reserved=max(Decimal("0"), window.reserved - released),
    )


def _consumption(kind: RuntimeQuotaKind, request: SettleRequest) -> Decimal | None:
    if kind == RuntimeQuotaKind.REQUESTS:
        return Decimal("1")
    if kind == RuntimeQuotaKind.TOKENS:
        return Decimal(request.input_tokens + request.output_tokens)
    if kind == RuntimeQuotaKind.CURRENCY and request.cost_usd is not None:
        return Decimal(str(request.cost_usd))
    return None


def _is_exhausted(window: RuntimeQuotaWindow) -> bool:
    return window.remaining is not None and window.remaining - window.config.safety_reserve - window.reserved <= 0


def _window_exclusions(
    account: AccountConfig,
    window: RuntimeQuotaWindow,
) -> tuple[EligibilityExclusion, ...]:
    config: Final = window.config
    if config.scope == RuntimeQuotaScope.CHANNEL:
        return (_exclusion(account=account, window=window, scope=EligibilityScope.CHANNEL),)
    if config.scope == RuntimeQuotaScope.MODEL and config.subject_id is not None:
        return (
            _exclusion(
                account=account,
                window=window,
                scope=EligibilityScope.MODEL,
                model=config.subject_id,
            ),
        )
    return tuple(
        _exclusion(
            account=account,
            window=window,
            scope=EligibilityScope.BILLING_ROUTE,
            model=deployment.public_model,
            deployment_id=deployment.litellm_model_id,
            billing_route_id=deployment.billing_route_id,
        )
        for deployment in account.deployments
        if config.scope == RuntimeQuotaScope.BILLING_ROUTE
        and config.subject_id is not None
        and deployment.billing_route_id == config.subject_id
    )


def _exclusion(
    account: AccountConfig,
    window: RuntimeQuotaWindow,
    scope: EligibilityScope,
    model: str | None = None,
    deployment_id: str | None = None,
    billing_route_id: str | None = None,
) -> EligibilityExclusion:
    return activate_exclusion(
        scope=scope,
        source=EligibilitySource.RESTRICTION,
        account_id=account.id,
        model=model,
        deployment_id=deployment_id,
        billing_route_id=billing_route_id,
        reason_code=window.config.reason_code,
        starts_at=window.config.observed_at,
        retry_at=window.retry_at,
    )


def _exclusion_key(exclusion: EligibilityExclusion) -> tuple[str, str | None, str | None, str | None, str]:
    return (
        exclusion.scope,
        exclusion.model,
        exclusion.deployment_id,
        exclusion.billing_route_id,
        exclusion.reason_code,
    )


def _merge_exclusion_group(group: tuple[EligibilityExclusion, ...]) -> EligibilityExclusion:
    first: Final = group[0]
    retry_values: Final = tuple(exclusion.retry_at for exclusion in group)
    retry_at: Final = (
        None
        if any(value is None for value in retry_values)
        else max(value for value in retry_values if value is not None)
    )
    return first.model_copy(
        update={
            "starts_at": min(exclusion.starts_at for exclusion in group),
            "retry_at": retry_at,
        }
    )
