"""验证渠道目录、解析结果、运行状态和活动事实的聚合总览。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final
from uuid import UUID

import psycopg
import pytest
from account_pool.catalog.models import AdministrativeState, ChannelList, ChannelSummary
from account_pool.health.models import HealthActivity
from account_pool.health.repository import (
    HealthActivityLoadSuccess,
    HealthPersistenceFailure,
    HealthPersistenceFailureCode,
)
from account_pool.models import (
    AccountConfig,
    AccountSnapshot,
    ChannelPriority,
    DeploymentConfig,
    Health,
    QuotaConfig,
    QuotaSnapshot,
    QuotaUnit,
    RouteEntry,
    RuntimeBillingMode,
)
from account_pool.overview import (
    AccountPoolOverview,
    AccountPoolOverviewFailure,
    AccountPoolOverviewService,
    OverviewFailureCode,
    ParserOverviewState,
)
from account_pool.parsing.models import (
    EffectivePrices,
    MeteredData,
    MeteredGroup,
    MeteredModelPrice,
    ParsedChannelData,
    ParserRunStatus,
    SubscriptionData,
    SubscriptionStatus,
)
from account_pool.parsing.service import EffectiveParserData, ParserDataFailure, ParserDataFailureCode

_CHANNEL_ID: Final = UUID("10000000-0000-0000-0000-000000000001")
_UNPROJECTED_CHANNEL_ID: Final = UUID("10000000-0000-0000-0000-000000000002")
_RUN_ID: Final = UUID("20000000-0000-0000-0000-000000000001")
_NOW: Final = datetime(2026, 8, 21, 9, 0, tzinfo=UTC)


class _Catalog:
    async def list_channels(self) -> ChannelList:
        return ChannelList(
            channels=(
                _channel(_CHANNEL_ID, "主渠道", ("gpt-5.6", "gpt-5.6-mini")),
                _channel(_UNPROJECTED_CHANNEL_ID, "待投影渠道", ("gpt-5.6",)),
            )
        )

    async def get_channel(self, channel_id: UUID) -> ChannelSummary | None:
        return next((channel for channel in (await self.list_channels()).channels if channel.channel_id == channel_id), None)


class _Runtime:
    def account_configs(self) -> tuple[AccountConfig, ...]:
        return (
            AccountConfig(
                id="primary",
                channel_id=_CHANNEL_ID,
                display_name="主渠道",
                provider="openai",
                base_url_display="https://example.com/v1",
                max_concurrency=4,
                priority=ChannelPriority.HIGH,
                quotas=QuotaConfig(unit=QuotaUnit.USD, total=100),
                deployments=(
                    DeploymentConfig(public_model="gpt-5.6", litellm_model_id="deployment-primary"),
                    DeploymentConfig(public_model="gpt-5.6-mini", litellm_model_id="deployment-mini"),
                ),
            ),
        )

    async def account_snapshots(self) -> tuple[AccountSnapshot, ...]:
        return (
            AccountSnapshot(
                account_id="primary",
                enabled=True,
                health=Health.HEALTHY,
                inflight=2,
                max_concurrency=4,
                cooldown_until=None,
                consecutive_failures=0,
                quota=QuotaSnapshot(unit=QuotaUnit.USD, total=75, five_hour=None, weekly=None),
            ),
        )

    def models(self) -> tuple[str, ...]:
        return ("gpt-5.6", "gpt-5.6-mini")

    async def route_table(self, model: str) -> tuple[RouteEntry, ...]:
        return (_route(model=model, available=model == "gpt-5.6"),)


class _ParserData:
    async def effective_data(self, channel_id: UUID) -> EffectiveParserData | ParserDataFailure:
        if channel_id == _UNPROJECTED_CHANNEL_ID:
            return ParserDataFailure(code=ParserDataFailureCode.RUN_NOT_FOUND, retryable=False)
        parsed: Final = ParsedChannelData(
            subscription=SubscriptionData(
                plan_name="专业版",
                status=SubscriptionStatus.ACTIVE,
                balance=Decimal("75.50"),
                currency="USD",
            ),
            metered=MeteredData(
                groups=(
                    MeteredGroup(
                        group_id="default",
                        models=(
                            MeteredModelPrice(
                                provider_model_id="gpt-5.6",
                                currency="USD",
                                unit="million_tokens",
                                input_price=Decimal("1"),
                                output_price=Decimal("2"),
                                effective_prices=EffectivePrices(
                                    input_price=Decimal("1"),
                                    output_price=Decimal("2"),
                                ),
                            ),
                        ),
                    ),
                )
            ),
            warnings=("需要复核缓存价格",),
        )
        return EffectiveParserData(
            channel_id=channel_id,
            parser_run_id=_RUN_ID,
            parser_id="openai_compatible",
            parser_version="1.0.0",
            parsed_at=_NOW,
            parser_status=ParserRunStatus.PARTIAL,
            raw_result=parsed,
            effective_result=parsed,
        )


class _HealthEvents:
    async def load_activity(self) -> HealthActivityLoadSuccess:
        return HealthActivityLoadSuccess(
            activities=(
                HealthActivity(
                    channel_id=_CHANNEL_ID,
                    account_id="primary",
                    model_id="gpt-5.6",
                    deployment_id="deployment-primary",
                    last_request_at=_NOW - timedelta(minutes=1),
                    last_success_at=_NOW - timedelta(minutes=2),
                    last_failure_at=_NOW - timedelta(hours=1),
                    last_probe_at=_NOW - timedelta(minutes=5),
                    updated_at=_NOW,
                ),
                HealthActivity(
                    channel_id=_CHANNEL_ID,
                    account_id="primary",
                    model_id="gpt-5.6-mini",
                    deployment_id="deployment-mini",
                    last_success_at=_NOW,
                    updated_at=_NOW,
                ),
            )
        )


class _UnavailableHealthEvents:
    async def load_activity(self) -> HealthActivityLoadSuccess | HealthPersistenceFailure:
        return HealthPersistenceFailure(code=HealthPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)


class _UnavailableCatalog:
    async def list_channels(self) -> ChannelList:
        raise psycopg.OperationalError("database unavailable")

    async def get_channel(self, channel_id: UUID) -> ChannelSummary | None:
        raise psycopg.OperationalError(f"database unavailable for {channel_id}")


@pytest.mark.asyncio
async def test_builds_aggregate_channel_overview_without_duplicating_credentials() -> None:
    result: Final = await AccountPoolOverviewService(
        catalog=_Catalog(),
        runtime=_Runtime(),
        parser_data=_ParserData(),
        health_events=_HealthEvents(),
    ).read()

    assert isinstance(result, AccountPoolOverview)
    primary, pending = result.channels
    assert result.channel_count == 2
    assert result.administratively_enabled_count == 2
    assert result.healthy_count == 1
    assert result.schedulable_count == 1
    assert result.configured_model_count == 2
    assert result.schedulable_model_count == 1
    assert (result.inflight, result.max_concurrency) == (2, 4)
    assert primary.account_id == "primary"
    assert primary.schedulable_models == ("gpt-5.6",)
    assert primary.unavailable_reason_codes == ("manual_pause",)
    assert primary.runtime is not None and primary.runtime.quota.total == 75
    assert primary.parser.parser_id == "openai_compatible"
    assert primary.parser.subscription is not None and primary.parser.subscription.balance == Decimal("75.50")
    assert primary.parser.metered is not None and primary.parser.metered.model_count == 1
    assert primary.activity.last_success_at == _NOW
    assert pending.runtime is None
    assert pending.parser.state == ParserOverviewState.NOT_RUN
    assert pending.unavailable_reason_codes == ("runtime_not_projected",)
    assert "credential_ref" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_marks_activity_unavailable_without_hiding_other_overview_data() -> None:
    result: Final = await AccountPoolOverviewService(
        catalog=_Catalog(),
        runtime=_Runtime(),
        parser_data=_ParserData(),
        health_events=_UnavailableHealthEvents(),
    ).read()

    assert isinstance(result, AccountPoolOverview)
    assert all(not channel.activity.persistence_available for channel in result.channels)
    assert result.channels[0].runtime is not None
    assert result.channels[0].parser.state == ParserOverviewState.LOADED


@pytest.mark.asyncio
async def test_reports_core_dependency_failure_instead_of_returning_an_empty_overview() -> None:
    result: Final = await AccountPoolOverviewService(
        catalog=_UnavailableCatalog(),
        runtime=_Runtime(),
        parser_data=_ParserData(),
        health_events=_HealthEvents(),
    ).read()

    assert result == AccountPoolOverviewFailure(
        code=OverviewFailureCode.DEPENDENCY_UNAVAILABLE,
        retryable=True,
    )


def _channel(channel_id: UUID, name: str, models: tuple[str, ...]) -> ChannelSummary:
    return ChannelSummary(
        channel_id=channel_id,
        display_name=name,
        provider="openai",
        base_url_display="https://example.com/v1",
        administrative_state=AdministrativeState.ENABLED,
        max_concurrency=4,
        priority=ChannelPriority.HIGH,
        weight=1,
        key_mask="sk-***test",
        binding_count=len(models),
        enabled_binding_count=len(models),
        models=models,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _route(*, model: str, available: bool) -> RouteEntry:
    return RouteEntry(
        account_id="primary",
        display_name="主渠道",
        provider="openai",
        base_url_display="https://example.com/v1",
        deployment_id=f"deployment-{model}",
        billing_mode=RuntimeBillingMode.PROVIDER_DECIDED,
        public_model=model,
        enabled=True,
        health=Health.HEALTHY,
        inflight=2,
        max_concurrency=4,
        cooldown_until=None,
        reason_code=None if available else "manual_pause",
        quota=QuotaSnapshot(unit=QuotaUnit.USD, total=75, five_hour=None, weekly=None),
        priority=ChannelPriority.HIGH,
        weight=1,
        available=available,
        unavailable_reason=None if available else "manual_pause",
    )
