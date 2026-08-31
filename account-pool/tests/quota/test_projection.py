"""验证套餐额度窗口的 scope 映射、稳定身份和失败策略。"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Final, Never
from uuid import UUID

import pytest
from account_pool.models import AccountConfig, DeploymentConfig, PoolConfig
from account_pool.parsing.models import (
    BillingMode,
    BillingRoute,
    ModelIdentity,
    ParsedChannelData,
    ParserRunStatus,
    QuotaKind,
    QuotaLimit,
    QuotaScope,
    QuotaWindowType,
    SubscriptionData,
    SubscriptionStatus,
)
from account_pool.parsing.service import (
    EffectiveParserData,
    EffectiveParserDataResult,
    ParserDataFailure,
    ParserDataFailureCode,
)
from account_pool.quota import ParserQuotaConfigEnricher, QuotaProjectionError, project_quota_windows

_CHANNEL_ID: Final = UUID("10000000-0000-0000-0000-000000000001")
_BINDING_A: Final = UUID("20000000-0000-0000-0000-000000000001")
_BINDING_B: Final = UUID("20000000-0000-0000-0000-000000000002")
_ROUTE_ID: Final = UUID("30000000-0000-0000-0000-000000000001")
_RUN_ID: Final = UUID("40000000-0000-0000-0000-000000000001")
_OBSERVED_AT: Final = datetime(2026, 8, 19, 0, 0, tzinfo=UTC)


class FakeParserData:
    def __init__(self, result: EffectiveParserDataResult) -> None:
        self._result: Final = result

    async def effective_data(self, channel_id: UUID) -> EffectiveParserDataResult:
        assert channel_id == _CHANNEL_ID
        return self._result

    async def history(self, channel_id: UUID, limit: int) -> Never:
        raise AssertionError((channel_id, limit))

    async def snapshot(self, channel_id: UUID) -> Never:
        raise AssertionError(channel_id)


def _account() -> AccountConfig:
    return AccountConfig(
        id="channel-a",
        channel_id=_CHANNEL_ID,
        display_name="Channel A",
        provider="test",
        base_url_display="https://example.test",
        max_concurrency=4,
        deployments=(
            DeploymentConfig(
                public_model="public-a",
                provider_model="provider-a",
                litellm_model_id="deployment-a",
                binding_id=_BINDING_A,
            ),
            DeploymentConfig(
                public_model="public-b",
                provider_model="provider-b",
                litellm_model_id="deployment-b",
                binding_id=_BINDING_B,
            ),
        ),
    )


def _parsed() -> ParsedChannelData:
    return ParsedChannelData(
        subscription=SubscriptionData(
            limits=(
                _limit(scope=QuotaScope.CHANNEL, subject_id=None, duration_seconds=18_000),
                _limit(scope=QuotaScope.MODEL, subject_id="provider-a", duration_seconds=604_800),
                _limit(scope=QuotaScope.GROUP, subject_id="premium", duration_seconds=2_592_000),
                _limit(scope=QuotaScope.GROUP, subject_id="unmapped", duration_seconds=86_400),
            )
        ),
        billing_routes=(
            BillingRoute(
                route_id=_ROUTE_ID,
                deployment_binding_id=_BINDING_A,
                mode=BillingMode.SUBSCRIPTION,
                provider_group_id="premium",
            ),
        ),
    )


def _limit(
    scope: QuotaScope,
    subject_id: str | None,
    duration_seconds: int,
) -> QuotaLimit:
    return QuotaLimit(
        scope=scope,
        subject_id=subject_id,
        kind=QuotaKind.TOKENS,
        window_type=QuotaWindowType.ROLLING,
        duration_seconds=duration_seconds,
        limit=Decimal("1000"),
        remaining=Decimal("250"),
        source="official-api",
        observed_at=_OBSERVED_AT,
    )


def _effective(parsed: ParsedChannelData) -> EffectiveParserData:
    return EffectiveParserData(
        channel_id=_CHANNEL_ID,
        parser_run_id=_RUN_ID,
        parser_id="fixture-parser",
        parser_version="1.0.0",
        parsed_at=_OBSERVED_AT,
        parser_status=ParserRunStatus.SUCCESS,
        raw_result=parsed,
        effective_result=parsed,
    )


def test_projects_channel_model_and_verified_group_windows() -> None:
    windows: Final = project_quota_windows(account=_account(), parsed=_parsed())

    assert tuple(window.scope for window in windows) == ("channel", "model", "billing_route")
    assert tuple(window.subject_id for window in windows) == (None, "public-a", str(_ROUTE_ID))
    assert tuple(window.reason_code for window in windows) == (
        "five_hour_exhausted",
        "weekly_exhausted",
        "monthly_exhausted",
    )
    assert all(window.remaining == 250 for window in windows)


def test_window_identity_is_stable_when_provider_remaining_changes() -> None:
    parsed: Final = _parsed()
    subscription: Final = parsed.subscription
    assert subscription is not None
    changed_limits: Final = (
        subscription.limits[0].model_copy(update={"remaining": Decimal("100")}),
        *subscription.limits[1:],
    )
    changed: Final = parsed.model_copy(
        update={"subscription": subscription.model_copy(update={"limits": changed_limits})}
    )

    original_ids: Final = tuple(window.window_id for window in project_quota_windows(_account(), parsed))
    changed_ids: Final = tuple(window.window_id for window in project_quota_windows(_account(), changed))

    assert changed_ids == original_ids


def test_window_identity_is_stable_when_provider_reorders_limits() -> None:
    parsed: Final = _parsed()
    subscription: Final = parsed.subscription
    assert subscription is not None
    reordered: Final = parsed.model_copy(
        update={"subscription": subscription.model_copy(update={"limits": tuple(reversed(subscription.limits))})}
    )

    original_ids: Final = frozenset(window.window_id for window in project_quota_windows(_account(), parsed))
    reordered_ids: Final = frozenset(window.window_id for window in project_quota_windows(_account(), reordered))

    assert reordered_ids == original_ids


def test_projection_preserves_decimal_precision() -> None:
    precise: Final = _limit(
        scope=QuotaScope.CHANNEL,
        subject_id=None,
        duration_seconds=18_000,
    ).model_copy(
        update={
            "limit": Decimal("1000.123456789123456789"),
            "remaining": Decimal("250.987654321987654321"),
        }
    )
    parsed: Final = ParsedChannelData(subscription=SubscriptionData(limits=(precise,)))

    window: Final = project_quota_windows(_account(), parsed)[0]

    assert window.limit == Decimal("1000.123456789123456789")
    assert window.remaining == Decimal("250.987654321987654321")


def test_subscription_balance_only_constrains_models_in_the_package() -> None:
    parsed: Final = ParsedChannelData(
        subscription=SubscriptionData(
            status=SubscriptionStatus.ACTIVE,
            models=(ModelIdentity(provider_model_id="provider-a"),),
            balance=Decimal("12"),
            currency="次",
        )
    )

    windows: Final = project_quota_windows(_account(), parsed)

    assert len(windows) == 1
    assert (windows[0].scope, windows[0].subject_id, windows[0].kind, windows[0].remaining) == (
        "model",
        "public-a",
        "provider_units",
        Decimal("12"),
    )


def test_exhausted_subscription_balance_only_marks_covered_model_unavailable() -> None:
    parsed: Final = ParsedChannelData(
        subscription=SubscriptionData(
            status=SubscriptionStatus.ACTIVE,
            models=(ModelIdentity(provider_model_id="provider-a"),),
            balance=Decimal("0"),
            currency="次",
        )
    )

    windows: Final = project_quota_windows(_account(), parsed)

    assert len(windows) == 1
    assert windows[0].subject_id == "public-a"
    assert windows[0].remaining == Decimal("0")


def test_projection_rejects_duplicate_semantic_windows() -> None:
    duplicate: Final = _limit(scope=QuotaScope.CHANNEL, subject_id=None, duration_seconds=18_000)
    parsed: Final = ParsedChannelData(subscription=SubscriptionData(limits=(duplicate, duplicate)))

    with pytest.raises(QuotaProjectionError, match="duplicate semantic windows"):
        project_quota_windows(_account(), parsed)


def test_reset_at_windows_use_provider_boundary_as_identity() -> None:
    first: Final = _limit(
        scope=QuotaScope.CHANNEL,
        subject_id=None,
        duration_seconds=18_000,
    ).model_copy(
        update={
            "window_type": QuotaWindowType.RESET_AT,
            "duration_seconds": None,
            "reset_at": datetime(2026, 8, 20, tzinfo=UTC),
        }
    )
    second: Final = first.model_copy(update={"reset_at": datetime(2026, 8, 27, tzinfo=UTC)})
    parsed: Final = ParsedChannelData(subscription=SubscriptionData(limits=(first, second)))

    windows: Final = project_quota_windows(_account(), parsed)

    assert len(frozenset(window.window_id for window in windows)) == 2


@pytest.mark.asyncio
async def test_enricher_applies_latest_effective_parser_data() -> None:
    parsed: Final = _parsed()
    enricher: Final = ParserQuotaConfigEnricher(FakeParserData(_effective(parsed)))

    enriched: Final = await enricher.enrich(PoolConfig(accounts=(_account(),)))

    assert enriched.accounts[0].quota_windows == project_quota_windows(_account(), parsed)


@pytest.mark.asyncio
async def test_missing_parser_run_preserves_catalog_config() -> None:
    config: Final = PoolConfig(accounts=(_account(),))
    enricher: Final = ParserQuotaConfigEnricher(
        FakeParserData(ParserDataFailure(code=ParserDataFailureCode.RUN_NOT_FOUND, retryable=False))
    )

    assert await enricher.enrich(config) == config


@pytest.mark.asyncio
async def test_database_failure_stops_runtime_projection() -> None:
    enricher: Final = ParserQuotaConfigEnricher(
        FakeParserData(ParserDataFailure(code=ParserDataFailureCode.DATABASE_UNAVAILABLE, retryable=True))
    )

    with pytest.raises(QuotaProjectionError, match="database_unavailable"):
        await enricher.enrich(PoolConfig(accounts=(_account(),)))
