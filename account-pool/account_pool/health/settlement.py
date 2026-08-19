"""将安全的请求结算信号分类为统一健康状态转换。"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Final, Self

from pydantic import Field, model_validator

from account_pool.models import FrozenModel, SettleRequest

MAX_RETRY_AFTER_SECONDS: Final = 86_400.0
DEFAULT_UNKNOWN_RATE_LIMIT_SECONDS: Final = 15.0
TRANSIENT_FAILURE_COOLDOWN_SECONDS: Final = 30.0
_CONCURRENCY_CODES: Final = frozenset(
    {
        "concurrency_limit",
        "concurrency_limit_exceeded",
        "too_many_concurrent_requests",
    }
)
_BALANCE_CODES: Final = frozenset(
    {
        "billing_hard_limit_reached",
        "credit_balance_too_low",
        "insufficient_balance",
        "insufficient_credits",
    }
)
_QUOTA_CODES: Final = frozenset({"insufficient_quota", "quota_exceeded"})


class HealthTransitionAction(StrEnum):
    SUCCESS = "success"
    DISABLE = "disable"
    COOLDOWN = "cooldown"
    OBSERVE = "observe"
    TRANSIENT_FAILURE = "transient_failure"


class SettlementHealthTransition(FrozenModel):
    action: HealthTransitionAction
    reason_code: str | None = Field(default=None, min_length=1, max_length=100)
    cooldown_until: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_action_fields(self) -> Self:
        needs_cooldown: Final = self.action in {
            HealthTransitionAction.COOLDOWN,
            HealthTransitionAction.TRANSIENT_FAILURE,
        }
        if needs_cooldown != (self.cooldown_until is not None):
            raise ValueError("cooldown actions require cooldown_until and other actions forbid it")
        if self.action == HealthTransitionAction.SUCCESS and self.reason_code is not None:
            raise ValueError("successful transition cannot contain a reason")
        if self.action != HealthTransitionAction.SUCCESS and self.reason_code is None:
            raise ValueError("failed transition requires a reason")
        return self


def classify_settlement(request: SettleRequest, now: float) -> SettlementHealthTransition:
    if request.success:
        return SettlementHealthTransition(action=HealthTransitionAction.SUCCESS)
    status: Final = request.status_code
    provider_code: Final = _normalized_provider_code(request.provider_error_code)
    if request.error_type == "provider_auth" or status == 401:
        return SettlementHealthTransition(
            action=HealthTransitionAction.DISABLE,
            reason_code="credential_invalid",
        )
    if status == 403:
        return SettlementHealthTransition(
            action=HealthTransitionAction.OBSERVE,
            reason_code="permission_denied",
        )
    if status == 404:
        return SettlementHealthTransition(
            action=HealthTransitionAction.OBSERVE,
            reason_code="model_not_found",
        )
    if status == 429:
        cooldown_seconds: Final = (
            request.retry_after_seconds
            if request.retry_after_seconds is not None
            else DEFAULT_UNKNOWN_RATE_LIMIT_SECONDS
        )
        return SettlementHealthTransition(
            action=HealthTransitionAction.COOLDOWN,
            reason_code=_rate_limit_reason(provider_code, request.error_type),
            cooldown_until=now + cooldown_seconds,
        )
    if status is not None and 400 <= status < 500:
        return SettlementHealthTransition(
            action=HealthTransitionAction.OBSERVE,
            reason_code="request_rejected",
        )
    return SettlementHealthTransition(
        action=HealthTransitionAction.TRANSIENT_FAILURE,
        reason_code="upstream_unavailable" if status is not None and status >= 500 else "transport_failure",
        cooldown_until=now + TRANSIENT_FAILURE_COOLDOWN_SECONDS,
    )


def parse_retry_after_seconds(value: str | None, now: datetime) -> float | None:
    if value is None:
        return None
    candidate: Final = value.strip()
    if not candidate:
        return None
    if candidate.isdecimal():
        return min(float(candidate), MAX_RETRY_AFTER_SECONDS)
    try:
        parsed: Final = parsedate_to_datetime(candidate)
    except (TypeError, ValueError, OverflowError):
        return None
    aware: Final = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    reference: Final = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    seconds: Final = max(0.0, (aware - reference).total_seconds())
    return min(seconds, MAX_RETRY_AFTER_SECONDS)


def _normalized_provider_code(value: str | None) -> str | None:
    if value is None:
        return None
    normalized: Final = value.strip().casefold()
    return normalized or None


def _rate_limit_reason(provider_code: str | None, error_type: str | None) -> str:
    if provider_code in _CONCURRENCY_CODES:
        return "concurrency_limited"
    if provider_code in _BALANCE_CODES:
        return "balance_signal_unscoped"
    if provider_code in _QUOTA_CODES:
        return "quota_signal_unscoped"
    if provider_code is None and error_type != "provider_rate_limit":
        return "rate_limit_unknown"
    return "rate_limited"
