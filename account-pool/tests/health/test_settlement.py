"""验证安全错误分类、Retry-After 和未知 429 的保守策略。"""

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from account_pool.health.settlement import (
    DEFAULT_UNKNOWN_RATE_LIMIT_SECONDS,
    MAX_RETRY_AFTER_SECONDS,
    HealthTransitionAction,
    classify_settlement,
    parse_retry_after_seconds,
)
from account_pool.models import SettleRequest

_NOW: Final = datetime(2026, 8, 20, 0, 0, tzinfo=UTC)
_NOW_EPOCH: Final = _NOW.timestamp()


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("120", 120.0),
        ("Wed, 20 Aug 2026 00:02:00 GMT", 120.0),
        ("Wed, 19 Aug 2026 23:59:00 GMT", 0.0),
        ("999999", MAX_RETRY_AFTER_SECONDS),
        ("invalid", None),
        (None, None),
    ),
)
def test_parse_retry_after_seconds(value: str | None, expected: float | None) -> None:
    assert parse_retry_after_seconds(value, _NOW) == expected


def test_unknown_429_uses_short_recoverable_cooldown() -> None:
    transition: Final = classify_settlement(
        SettleRequest(lease_id="lease", success=False, status_code=429),
        _NOW_EPOCH,
    )

    assert transition.action == HealthTransitionAction.COOLDOWN
    assert transition.reason_code == "rate_limit_unknown"
    assert transition.cooldown_until == _NOW_EPOCH + DEFAULT_UNKNOWN_RATE_LIMIT_SECONDS


def test_retry_after_and_safe_provider_code_classify_concurrency() -> None:
    transition: Final = classify_settlement(
        SettleRequest(
            lease_id="lease",
            success=False,
            status_code=429,
            provider_error_code="concurrency_limit_exceeded",
            retry_after_seconds=90,
        ),
        _NOW_EPOCH,
    )

    assert transition.reason_code == "concurrency_limited"
    assert transition.cooldown_until == _NOW_EPOCH + 90


@pytest.mark.parametrize(
    ("status_code", "action", "reason_code"),
    (
        (401, HealthTransitionAction.DISABLE, "credential_invalid"),
        (403, HealthTransitionAction.OBSERVE, "permission_denied"),
        (404, HealthTransitionAction.OBSERVE, "model_not_found"),
        (500, HealthTransitionAction.TRANSIENT_FAILURE, "upstream_unavailable"),
    ),
)
def test_status_scope_avoids_disabling_channel_for_model_or_permission_failures(
    status_code: int,
    action: HealthTransitionAction,
    reason_code: str,
) -> None:
    transition: Final = classify_settlement(
        SettleRequest(lease_id="lease", success=False, status_code=status_code),
        _NOW_EPOCH,
    )

    assert transition.action == action
    assert transition.reason_code == reason_code


def test_retry_after_http_date_uses_utc_offset() -> None:
    offset_now: Final = _NOW.astimezone(UTC) - timedelta(hours=1)
    assert parse_retry_after_seconds("Thu, 20 Aug 2026 00:00:00 GMT", offset_now) == 3600
