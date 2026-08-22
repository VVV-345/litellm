"""验证额度窗口运行时的匹配、扣减、校准和限制生成。"""

from decimal import Decimal
from typing import Final

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
from account_pool.quota.runtime import (
    QuotaReserveRejected,
    QuotaReserveSuccess,
    QuotaUsageDelta,
    RuntimeQuotaWindow,
    apply_quota_usage,
    quota_rejection,
    quota_window_exclusions,
    reconcile_quota_windows,
    release_quota_capacity,
    reserve_quota_capacity,
)


def _config(
    window_id: str,
    scope: RuntimeQuotaScope = RuntimeQuotaScope.CHANNEL,
    subject_id: str | None = None,
    remaining: str = "100",
    observed_at: float = 1_000,
    reset_at: float | None = None,
    duration_seconds: int | None = 18_000,
    kind: RuntimeQuotaKind = RuntimeQuotaKind.TOKENS,
    reason_code: str = "five_hour_exhausted",
) -> QuotaWindowConfig:
    return QuotaWindowConfig(
        window_id=window_id,
        scope=scope,
        subject_id=subject_id,
        kind=kind,
        window_type=(RuntimeQuotaWindowType.RESET_AT if reset_at is not None else RuntimeQuotaWindowType.ROLLING),
        duration_seconds=None if reset_at is not None else duration_seconds,
        limit=Decimal("100"),
        remaining=Decimal(remaining),
        reset_at=reset_at,
        observed_at=observed_at,
        source="provider-api",
        reason_code=reason_code,
    )


def _account(windows: tuple[QuotaWindowConfig, ...]) -> AccountConfig:
    return AccountConfig(
        id="channel-a",
        display_name="Channel A",
        provider="test",
        base_url_display="https://example.test",
        max_concurrency=2,
        quota_windows=windows,
        deployments=(
            DeploymentConfig(
                public_model="model-a",
                litellm_model_id="deployment-a",
                billing_route_id="route-a",
            ),
            DeploymentConfig(public_model="model-b", litellm_model_id="deployment-b"),
        ),
    )


def _lease(model: str = "model-a", billing_route_id: str | None = "route-a") -> Lease:
    return Lease(
        lease_id="lease-a",
        request_id="request-a",
        account_id="channel-a",
        deployment_id="deployment-a",
        public_model=model,
        billing_route_id=billing_route_id,
        expires_at=2_000,
        absolute_expires_at=3_000,
    )


def test_reconcile_preserves_local_usage_until_provider_snapshot_changes() -> None:
    configured: Final = _config("window-a")
    previous: Final = RuntimeQuotaWindow(
        config=configured,
        remaining=Decimal("40"),
        retry_at=19_000,
    )

    unchanged: Final = reconcile_quota_windows((previous,), (configured,))[0]
    refreshed: Final = reconcile_quota_windows(
        (previous,),
        (configured.model_copy(update={"remaining": Decimal("90"), "observed_at": 2_000}),),
    )[0]

    assert unchanged.remaining == Decimal("40")
    assert refreshed.remaining == Decimal("90")
    assert refreshed.retry_at == 20_000


def test_new_provider_snapshot_preserves_active_reservations_and_post_snapshot_usage() -> None:
    configured: Final = _config("window-a", observed_at=1_000)
    previous: Final = RuntimeQuotaWindow(
        config=configured,
        remaining=Decimal("40"),
        retry_at=19_000,
        usage=(
            QuotaUsageDelta(amount=Decimal("10"), occurred_at=1_050),
            QuotaUsageDelta(amount=Decimal("5"), occurred_at=1_250),
        ),
        reserved=Decimal("20"),
    )
    refreshed: Final = configured.model_copy(update={"remaining": Decimal("80"), "observed_at": 1_200})

    reconciled: Final = reconcile_quota_windows((previous,), (refreshed,))[0]

    assert reconciled.remaining == Decimal("75")
    assert reconciled.usage == (QuotaUsageDelta(amount=Decimal("5"), occurred_at=1_250),)
    assert reconciled.reserved == Decimal("20")


def test_usage_only_decrements_matching_channel_model_and_route_windows() -> None:
    configs: Final = (
        _config("channel"),
        _config("model-a", scope=RuntimeQuotaScope.MODEL, subject_id="model-a"),
        _config("model-b", scope=RuntimeQuotaScope.MODEL, subject_id="model-b"),
        _config("route-a", scope=RuntimeQuotaScope.BILLING_ROUTE, subject_id="route-a"),
    )
    windows: Final = reconcile_quota_windows((), configs)

    updated: Final = apply_quota_usage(
        windows=windows,
        reservations=(),
        lease=_lease(),
        request=SettleRequest(
            lease_id="lease-a",
            success=True,
            input_tokens=25,
            output_tokens=5,
        ),
        now=1_100,
    )

    assert tuple(window.remaining for window in updated) == (
        Decimal("70"),
        Decimal("70"),
        Decimal("100"),
        Decimal("70"),
    )


def test_request_and_currency_windows_use_only_available_usage() -> None:
    configs: Final = (
        _config("requests", kind=RuntimeQuotaKind.REQUESTS),
        _config("currency", kind=RuntimeQuotaKind.CURRENCY),
        _config("credits", kind=RuntimeQuotaKind.CREDITS),
    )

    updated: Final = apply_quota_usage(
        windows=reconcile_quota_windows((), configs),
        reservations=(),
        lease=_lease(),
        request=SettleRequest(lease_id="lease-a", success=True, cost_usd=1.25),
        now=1_100,
    )

    assert tuple(window.remaining for window in updated) == (
        Decimal("99"),
        Decimal("98.75"),
        Decimal("100"),
    )


def test_exhausted_windows_create_scope_specific_restrictions() -> None:
    configs: Final = (
        _config("channel", remaining="0"),
        _config(
            "model",
            scope=RuntimeQuotaScope.MODEL,
            subject_id="model-a",
            remaining="0",
            reason_code="weekly_exhausted",
        ),
        _config(
            "route",
            scope=RuntimeQuotaScope.BILLING_ROUTE,
            subject_id="route-a",
            remaining="0",
            reason_code="monthly_exhausted",
        ),
    )
    account: Final = _account(configs)
    windows: Final = reconcile_quota_windows((), configs)

    exclusions: Final = quota_window_exclusions(account=account, windows=windows)

    assert tuple(exclusion.scope for exclusion in exclusions) == ("billing_route", "channel", "model")
    assert {exclusion.reason_code for exclusion in exclusions} == {
        "five_hour_exhausted",
        "weekly_exhausted",
        "monthly_exhausted",
    }
    assert next(exclusion for exclusion in exclusions if exclusion.scope == "billing_route").deployment_id == (
        "deployment-a"
    )


def test_quota_rejection_ends_at_reset_and_success_rebases_the_window() -> None:
    config: Final = _config("window-a", remaining="0", reset_at=1_200)
    windows: Final = reconcile_quota_windows((), (config,))

    before: Final = quota_rejection(windows, public_model="model-a", billing_route_id=None, now=1_100)
    after: Final = quota_rejection(windows, public_model="model-a", billing_route_id=None, now=1_200)
    rebased: Final = apply_quota_usage(
        windows=windows,
        reservations=(),
        lease=_lease(billing_route_id=None),
        request=SettleRequest(lease_id="lease-a", success=True, input_tokens=10),
        now=1_200,
    )[0]

    assert before == "five_hour_exhausted"
    assert after is None
    assert rebased.remaining == Decimal("90")
    assert rebased.retry_at is None


def test_rolling_window_expires_usage_deltas_individually() -> None:
    config: Final = _config("window-a", remaining="100", duration_seconds=100)
    initial: Final = reconcile_quota_windows((), (config,))
    first: Final = apply_quota_usage(
        windows=initial,
        reservations=(),
        lease=_lease(billing_route_id=None),
        request=SettleRequest(lease_id="lease-a", success=True, input_tokens=60),
        now=1_010,
    )
    exhausted: Final = apply_quota_usage(
        windows=first,
        reservations=(),
        lease=_lease(billing_route_id=None),
        request=SettleRequest(lease_id="lease-a", success=True, input_tokens=50),
        now=1_050,
    )[0]

    after_first_expiry: Final = apply_quota_usage(
        windows=(exhausted,),
        reservations=(),
        lease=_lease(billing_route_id=None),
        request=SettleRequest(lease_id="lease-a", success=True),
        now=1_110,
    )[0]

    assert exhausted.remaining == Decimal("0")
    assert exhausted.retry_at == 1_110
    assert after_first_expiry.remaining == Decimal("50")
    assert after_first_expiry.retry_at is None


def test_failed_half_open_probe_does_not_assume_provider_window_reset() -> None:
    config: Final = _config("window-a", remaining="0", reset_at=1_200)
    windows: Final = reconcile_quota_windows((), (config,))

    unchanged: Final = apply_quota_usage(
        windows=windows,
        reservations=(),
        lease=_lease(billing_route_id=None),
        request=SettleRequest(lease_id="lease-a", success=False, status_code=429),
        now=1_200,
    )[0]

    assert unchanged.remaining == Decimal("0")
    assert unchanged.retry_at == 1_200


def test_failed_reserved_half_open_probe_preserves_exhausted_window() -> None:
    config: Final = _config("window-a", remaining="0", reset_at=1_200, kind=RuntimeQuotaKind.REQUESTS)
    windows: Final = reconcile_quota_windows((), (config,))
    reserved: Final = reserve_quota_capacity(
        windows=windows,
        public_model="model-a",
        billing_route_id=None,
        estimated_tokens=0,
        now=1_200,
    )
    assert isinstance(reserved, QuotaReserveSuccess)

    failed: Final = apply_quota_usage(
        windows=reserved.windows,
        reservations=reserved.reservations,
        lease=_lease(billing_route_id=None),
        request=SettleRequest(lease_id="lease-a", success=False, status_code=429),
        now=1_200,
    )[0]

    assert failed.remaining == Decimal("0")
    assert failed.reserved == 0
    assert failed.retry_at == 1_200


def test_reservations_prevent_concurrent_request_and_token_oversubscription() -> None:
    configs: Final = (
        _config("requests", remaining="1", kind=RuntimeQuotaKind.REQUESTS),
        _config("tokens", remaining="50", kind=RuntimeQuotaKind.TOKENS),
    )
    windows: Final = reconcile_quota_windows((), configs)

    first: Final = reserve_quota_capacity(
        windows=windows,
        public_model="model-a",
        billing_route_id=None,
        estimated_tokens=40,
        now=1_100,
    )
    assert isinstance(first, QuotaReserveSuccess)
    second: Final = reserve_quota_capacity(
        windows=first.windows,
        public_model="model-a",
        billing_route_id=None,
        estimated_tokens=20,
        now=1_100,
    )

    assert isinstance(second, QuotaReserveRejected)
    assert second.reason_code == "five_hour_exhausted"
    released: Final = release_quota_capacity(first.windows, first.reservations)
    assert all(window.reserved == 0 for window in released)


def test_settlement_replaces_reservation_with_actual_usage() -> None:
    windows: Final = reconcile_quota_windows((), (_config("tokens", remaining="100"),))
    reserved: Final = reserve_quota_capacity(
        windows=windows,
        public_model="model-a",
        billing_route_id=None,
        estimated_tokens=80,
        now=1_100,
    )
    assert isinstance(reserved, QuotaReserveSuccess)

    settled: Final = apply_quota_usage(
        windows=reserved.windows,
        reservations=reserved.reservations,
        lease=_lease(billing_route_id=None),
        request=SettleRequest(lease_id="lease-a", success=True, input_tokens=25, output_tokens=5),
        now=1_110,
    )[0]

    assert settled.reserved == 0
    assert settled.remaining == Decimal("70")


def test_safety_reserve_is_never_available_for_new_requests() -> None:
    config: Final = _config("tokens", remaining="100").model_copy(update={"safety_reserve": Decimal("25")})
    windows: Final = reconcile_quota_windows((), (config,))

    accepted: Final = reserve_quota_capacity(
        windows=windows,
        public_model="model-a",
        billing_route_id=None,
        estimated_tokens=75,
        now=1_100,
    )
    assert isinstance(accepted, QuotaReserveSuccess)
    rejected: Final = reserve_quota_capacity(
        windows=release_quota_capacity(accepted.windows, accepted.reservations),
        public_model="model-a",
        billing_route_id=None,
        estimated_tokens=76,
        now=1_100,
    )

    assert isinstance(rejected, QuotaReserveRejected)
    assert rejected.reason_code == "five_hour_exhausted"


def test_successful_fixed_probe_advances_to_next_known_period() -> None:
    config: Final = _config("fixed", remaining="0", reset_at=1_200).model_copy(
        update={"window_type": RuntimeQuotaWindowType.FIXED, "duration_seconds": 100}
    )
    windows: Final = reconcile_quota_windows((), (config,))

    first_period: Final = apply_quota_usage(
        windows=windows,
        reservations=(),
        lease=_lease(billing_route_id=None),
        request=SettleRequest(lease_id="lease-a", success=True, input_tokens=10),
        now=1_200,
    )[0]
    second_period: Final = apply_quota_usage(
        windows=(first_period,),
        reservations=(),
        lease=_lease(billing_route_id=None),
        request=SettleRequest(lease_id="lease-a", success=True, input_tokens=10),
        now=1_300,
    )[0]

    assert first_period.remaining == Decimal("90")
    assert first_period.retry_at == 1_300
    assert second_period.remaining == Decimal("90")
    assert second_period.retry_at == 1_400
