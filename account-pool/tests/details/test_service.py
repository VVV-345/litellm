"""验证渠道详情组合各模块结果且不会因辅助区域失败丢失基础配置。"""

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

import pytest
from account_pool.catalog.models import AdministrativeState, BindingOwnership
from account_pool.details import ChannelAggregateDetail, ChannelAggregateService
from account_pool.events import EventLogFailure, EventLogFailureCode, EventQuery
from account_pool.health.service import ChannelHealthDetailFailure
from account_pool.models import (
    ChannelPriority,
    Health,
    QuotaConfig,
    QuotaSnapshot,
    QuotaUnit,
    RouteEntry,
    RuntimeBillingMode,
)
from account_pool.overview import (
    AccountPoolOverview,
    ChannelActivityOverview,
    ChannelOverview,
    ParserOverview,
    ParserOverviewState,
)
from account_pool.parsing.service import ParserDataFailure, ParserDataFailureCode
from account_pool.sync.contracts import ChannelBindingMutation, ChannelDetail

_CHANNEL_ID: Final = UUID("10000000-0000-0000-0000-000000000001")
_NOW: Final = datetime(2026, 8, 22, tzinfo=UTC)


class _Channels:
    async def detail(self, channel_id: UUID) -> ChannelDetail:
        return ChannelDetail(
            channel_id=channel_id,
            display_name="主渠道",
            provider="openai_compatible",
            group="default",
            base_url_display="https://example.com/v1",
            administrative_state=AdministrativeState.ENABLED,
            max_concurrency=4,
            priority=ChannelPriority.HIGH,
            weight=2,
            quotas=QuotaConfig(),
            key_mask="sk-***test",
            bindings=(
                ChannelBindingMutation(
                    binding_id=UUID("20000000-0000-0000-0000-000000000002"),
                    public_model="gpt-5.6",
                    provider_model="openai/gpt-5.6",
                    litellm_deployment_id="deployment-1",
                    ownership=BindingOwnership.POOL_MANAGED,
                ),
            ),
        )


class _Overview:
    async def read(self) -> AccountPoolOverview:
        channel: Final = ChannelOverview(
            channel_id=_CHANNEL_ID,
            account_id="primary",
            display_name="主渠道",
            provider="openai_compatible",
            group="default",
            base_url_display="https://example.com/v1",
            key_mask="sk-***test",
            administrative_state=AdministrativeState.ENABLED,
            priority=ChannelPriority.HIGH,
            configured_models=("gpt-5.6",),
            schedulable_models=("gpt-5.6",),
            binding_count=1,
            enabled_binding_count=1,
            parser=ParserOverview(state=ParserOverviewState.NOT_RUN),
            activity=ChannelActivityOverview(persistence_available=False),
        )
        return AccountPoolOverview(
            channels=(channel,),
            channel_count=1,
            administratively_enabled_count=1,
            healthy_count=0,
            schedulable_count=1,
            configured_model_count=1,
            schedulable_model_count=1,
            inflight=0,
            max_concurrency=0,
        )


class _Parser:
    async def effective_data(self, channel_id: UUID) -> ParserDataFailure:
        return ParserDataFailure(code=ParserDataFailureCode.RUN_NOT_FOUND, retryable=False)


class _Health:
    async def read_channel(self, channel_id: UUID) -> ChannelHealthDetailFailure:
        return ChannelHealthDetailFailure(code="runtime_unavailable", retryable=True)


class _Routing:
    async def route_table(self, model: str) -> tuple[RouteEntry, ...]:
        return (
            RouteEntry(
                account_id="primary",
                display_name="主渠道",
                provider="openai_compatible",
                base_url_display="https://example.com/v1",
                deployment_id="deployment-1",
                billing_mode=RuntimeBillingMode.PROVIDER_DECIDED,
                public_model=model,
                enabled=True,
                health=Health.HEALTHY,
                inflight=0,
                max_concurrency=4,
                cooldown_until=None,
                reason_code=None,
                quota=QuotaSnapshot(unit=QuotaUnit.TOKENS, total=None, five_hour=None, weekly=None),
                priority=300,
                weight=2,
                available=True,
                unavailable_reason=None,
                position=1,
            ),
        )


class _Events:
    query: EventQuery | None = None

    async def list_events(self, query: EventQuery) -> EventLogFailure:
        self.query = query
        return EventLogFailure(code=EventLogFailureCode.DATABASE_UNAVAILABLE, retryable=True)


@pytest.mark.asyncio
async def test_combines_loaded_sections_and_preserves_typed_section_failures() -> None:
    events: Final = _Events()
    result: Final = await ChannelAggregateService(
        channels=_Channels(),
        overview=_Overview(),
        parser_data=_Parser(),
        health=_Health(),
        routing=_Routing(),
        events=events,
    ).read_channel(_CHANNEL_ID)

    assert isinstance(result, ChannelAggregateDetail)
    assert result.channel.key_mask == "sk-***test"
    assert result.overview.status == "loaded"
    assert result.routes.data is not None and result.routes.data[0].position == 1
    assert result.parser.failure is not None and result.parser.failure.code == "run_not_found"
    assert result.health.failure is not None and result.health.failure.retryable
    assert result.events.failure is not None and result.events.failure.code == "database_unavailable"
    assert events.query == EventQuery(channel_id=_CHANNEL_ID, limit=8)
    assert "credential" not in result.model_dump_json().casefold()
