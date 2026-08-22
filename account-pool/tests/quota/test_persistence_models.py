"""验证额度持久化模型的幂等标识、精确 usage 和安全快照约束。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final
from uuid import UUID

import pytest
from account_pool.models import (
    AccountConfig,
    DeploymentConfig,
    Lease,
    QuotaWindowConfig,
    RuntimeQuotaKind,
    RuntimeQuotaScope,
    RuntimeQuotaWindowType,
    SettleRequest,
)
from account_pool.quota.persistence_models import (
    QuotaGenerationStatus,
    QuotaRuntimeGeneration,
    QuotaWindowRuntimeSnapshot,
    build_quota_usage_events,
    build_quota_window_snapshot,
    quota_provider_fingerprint,
    quota_usage_event_id,
    restore_quota_window,
)
from account_pool.quota.runtime import RuntimeQuotaWindow
from pydantic import ValidationError

_GENERATION_ID: Final = UUID("50000000-0000-0000-0000-000000000001")
_OTHER_GENERATION_ID: Final = UUID("50000000-0000-0000-0000-000000000002")
_CHANNEL_ID: Final = UUID("50000000-0000-0000-0000-000000000003")
_NOW: Final = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def _window(
    *,
    window_id: str,
    kind: RuntimeQuotaKind,
    scope: RuntimeQuotaScope = RuntimeQuotaScope.CHANNEL,
    subject_id: str | None = None,
    remaining: Decimal = Decimal("1000.123456789123456789123456789123456789"),
) -> RuntimeQuotaWindow:
    config: Final = QuotaWindowConfig(
        window_id=window_id,
        scope=scope,
        subject_id=subject_id,
        kind=kind,
        window_type=RuntimeQuotaWindowType.ROLLING,
        duration_seconds=18_000,
        limit=Decimal("2000.123456789123456789123456789123456789"),
        remaining=remaining,
        safety_reserve=Decimal("0.000000000000000000000000000000000001"),
        observed_at=_NOW.timestamp(),
        source="provider-api",
        reason_code="five_hour_exhausted",
    )
    return RuntimeQuotaWindow(
        config=config,
        remaining=remaining,
        retry_at=(_NOW + timedelta(hours=5)).timestamp(),
    )


def _account(windows: tuple[RuntimeQuotaWindow, ...]) -> AccountConfig:
    return AccountConfig(
        id="channel-a",
        channel_id=_CHANNEL_ID,
        display_name="Channel A",
        provider="test",
        base_url_display="https://example.test",
        max_concurrency=2,
        quota_windows=tuple(window.config for window in windows),
        deployments=(
            DeploymentConfig(
                public_model="model-a",
                litellm_model_id="deployment-a",
                billing_route_id="route-a",
            ),
        ),
    )


def _lease() -> Lease:
    return Lease(
        lease_id="lease-a",
        request_id="request-a",
        account_id="channel-a",
        deployment_id="deployment-a",
        public_model="model-a",
        billing_route_id="route-a",
        expires_at=(_NOW + timedelta(minutes=2)).timestamp(),
        absolute_expires_at=(_NOW + timedelta(hours=1)).timestamp(),
    )


def test_usage_event_id_is_stable_per_generation_lease_and_window() -> None:
    first: Final = quota_usage_event_id(_GENERATION_ID, "lease-a", "window-a")
    repeated: Final = quota_usage_event_id(_GENERATION_ID, "lease-a", "window-a")
    next_generation: Final = quota_usage_event_id(_OTHER_GENERATION_ID, "lease-a", "window-a")

    assert first == repeated
    assert first != next_generation


def test_usage_builder_records_only_matching_and_measurable_dimensions() -> None:
    windows: Final = (
        _window(window_id="requests", kind=RuntimeQuotaKind.REQUESTS),
        _window(
            window_id="tokens",
            kind=RuntimeQuotaKind.TOKENS,
            scope=RuntimeQuotaScope.MODEL,
            subject_id="model-a",
        ),
        _window(
            window_id="currency",
            kind=RuntimeQuotaKind.CURRENCY,
            scope=RuntimeQuotaScope.BILLING_ROUTE,
            subject_id="route-a",
        ),
        _window(
            window_id="other-model",
            kind=RuntimeQuotaKind.TOKENS,
            scope=RuntimeQuotaScope.MODEL,
            subject_id="model-b",
        ),
        _window(window_id="provider-units", kind=RuntimeQuotaKind.PROVIDER_UNITS),
    )
    events: Final = build_quota_usage_events(
        generation_id=_GENERATION_ID,
        account=_account(windows),
        lease=_lease(),
        request=SettleRequest(
            lease_id="lease-a",
            success=True,
            input_tokens=25,
            output_tokens=5,
            cost_usd=1.25,
        ),
        windows=windows,
        occurred_at=_NOW,
    )

    assert tuple((event.window_id, event.amount) for event in events) == (
        ("requests", Decimal("1")),
        ("tokens", Decimal("30")),
        ("currency", Decimal("1.25")),
    )
    assert all(event.channel_id == _CHANNEL_ID for event in events)
    assert all(event.lease_id == "lease-a" and event.request_id == "request-a" for event in events)


def test_snapshot_preserves_exact_decimal_state_and_provider_fingerprint() -> None:
    window: Final = _window(window_id="window-a", kind=RuntimeQuotaKind.CURRENCY)

    snapshot: Final = build_quota_window_snapshot(
        generation_id=_GENERATION_ID,
        account=_account((window,)),
        window=window,
        captured_at=_NOW,
    )

    assert snapshot.remaining_value == Decimal("1000.123456789123456789123456789123456789")
    assert snapshot.provider_remaining_value == Decimal("1000.123456789123456789123456789123456789")
    assert snapshot.safety_reserve_value == Decimal("0.000000000000000000000000000000000001")
    assert snapshot.provider_fingerprint == quota_provider_fingerprint(window.config)
    assert snapshot.provider_observed_at == _NOW
    assert snapshot.retry_at == _NOW + timedelta(hours=5)

    restored: Final = restore_quota_window(snapshot, (), _NOW)
    assert restored.config.remaining == snapshot.provider_remaining_value


def test_reserved_snapshot_requires_an_expiry_boundary() -> None:
    window: Final = _window(window_id="window-a", kind=RuntimeQuotaKind.TOKENS)
    invalid: Final = {
        **build_quota_window_snapshot(
            generation_id=_GENERATION_ID,
            account=_account((window,)),
            window=window,
            captured_at=_NOW,
        ).model_dump(),
        "reserved_value": Decimal("10"),
    }

    with pytest.raises(ValidationError, match="reservation_expires_at"):
        QuotaWindowRuntimeSnapshot.model_validate(invalid)


def test_generation_lifecycle_rejects_active_without_activation_time() -> None:
    with pytest.raises(ValidationError, match="activated_at"):
        QuotaRuntimeGeneration(
            generation_id=_GENERATION_ID,
            status=QuotaGenerationStatus.ACTIVE,
            created_at=_NOW,
        )


def test_generation_rejects_isolation_deadline_before_creation() -> None:
    with pytest.raises(ValidationError, match="isolation"):
        QuotaRuntimeGeneration(
            generation_id=_GENERATION_ID,
            status=QuotaGenerationStatus.INITIALIZING,
            created_at=_NOW,
            isolation_until=_NOW - timedelta(seconds=1),
        )
