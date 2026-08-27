"""并行读取各权威模块并组合单个渠道的聚合详情。"""

from __future__ import annotations

import asyncio
from typing import Final, Protocol, TypeVar
from uuid import UUID

from redis.exceptions import RedisError

from account_pool.details.models import (
    ChannelAggregateDetail,
    ChannelAggregateFailure,
    ChannelAggregateResult,
    DetailSection,
    DetailSectionFailure,
)
from account_pool.events.models import EventLogEntry, EventLogFailure, EventLogPage, EventQuery
from account_pool.events.repository import EventLogReader
from account_pool.health.service import (
    ChannelHealthDetail,
    ChannelHealthDetailFailure,
    ChannelHealthDetailReader,
    ChannelHealthDetailSuccess,
)
from account_pool.models import RouteEntry
from account_pool.overview.models import AccountPoolOverview, AccountPoolOverviewFailure, ChannelOverview
from account_pool.overview.service import AccountPoolOverviewReader
from account_pool.parsing.service import EffectiveParserData, ParserDataFailure
from account_pool.sync.service import ChannelDetail, ChannelManagementFailure

SectionValue = TypeVar("SectionValue")
RECENT_EVENT_LIMIT: Final = 8


class DetailChannelSource(Protocol):
    async def detail(self, channel_id: UUID) -> ChannelDetail | ChannelManagementFailure: ...


class DetailParserSource(Protocol):
    async def effective_data(self, channel_id: UUID) -> EffectiveParserData | ParserDataFailure: ...


class DetailRoutingSource(Protocol):
    async def route_table(self, model: str) -> tuple[RouteEntry, ...]: ...


class ChannelAggregateReader(Protocol):
    async def read_channel(self, channel_id: UUID) -> ChannelAggregateResult: ...


class ChannelAggregateService:
    def __init__(
        self,
        *,
        channels: DetailChannelSource,
        overview: AccountPoolOverviewReader,
        parser_data: DetailParserSource | None,
        health: ChannelHealthDetailReader,
        routing: DetailRoutingSource,
        events: EventLogReader | None,
    ) -> None:
        self._channels: Final = channels
        self._overview: Final = overview
        self._parser_data: Final = parser_data
        self._health: Final = health
        self._routing: Final = routing
        self._events: Final = events

    async def read_channel(self, channel_id: UUID) -> ChannelAggregateResult:
        channel: Final = await self._channels.detail(channel_id)
        if isinstance(channel, ChannelManagementFailure):
            return ChannelAggregateFailure(code=channel.code, retryable=channel.retryable)
        overview, parser, health, events = await asyncio.gather(
            self._overview.read(),
            self._parser(channel_id),
            self._health.read_channel(channel_id),
            self._event_page(channel_id),
        )
        overview_section: Final = _overview_section(channel_id, overview)
        return ChannelAggregateDetail(
            channel=channel,
            overview=overview_section,
            parser=_parser_section(parser),
            health=_health_section(health),
            routes=await self._routes_section(channel, overview_section),
            events=_event_section(events),
        )

    async def _parser(self, channel_id: UUID) -> EffectiveParserData | ParserDataFailure | None:
        if self._parser_data is None:
            return None
        return await self._parser_data.effective_data(channel_id)

    async def _event_page(self, channel_id: UUID) -> EventLogPage | EventLogFailure | None:
        if self._events is None:
            return None
        return await self._events.list_events(EventQuery(channel_id=channel_id, limit=RECENT_EVENT_LIMIT))

    async def _routes_section(
        self,
        channel: ChannelDetail,
        overview: DetailSection[ChannelOverview],
    ) -> DetailSection[tuple[RouteEntry, ...]]:
        if overview.status != "loaded" or overview.data is None:
            return _unavailable("overview_unavailable", True)
        account_id: Final = overview.data.account_id
        if account_id is None:
            return _unavailable("runtime_not_projected", True)
        models: Final = tuple(dict.fromkeys(binding.public_model for binding in channel.bindings))
        try:
            route_groups: Final = await asyncio.gather(*(self._routing.route_table(model) for model in models))
        except RedisError:
            return _unavailable("runtime_unavailable", True)
        routes: Final = tuple(route for group in route_groups for route in group if route.account_id == account_id)
        return DetailSection(status="loaded", data=routes)


def _overview_section(
    channel_id: UUID,
    result: AccountPoolOverview | AccountPoolOverviewFailure,
) -> DetailSection[ChannelOverview]:
    if isinstance(result, AccountPoolOverviewFailure):
        return _unavailable(result.code, result.retryable)
    channel: Final = next((item for item in result.channels if item.channel_id == channel_id), None)
    if channel is None:
        return _unavailable("overview_channel_not_found", False)
    return DetailSection(status="loaded", data=channel)


def _parser_section(result: EffectiveParserData | ParserDataFailure | None) -> DetailSection[EffectiveParserData]:
    if result is None:
        return _unavailable("parser_not_configured", True)
    if isinstance(result, ParserDataFailure):
        return _unavailable(result.code, result.retryable)
    return DetailSection(status="loaded", data=result)


def _health_section(
    result: ChannelHealthDetailSuccess | ChannelHealthDetailFailure,
) -> DetailSection[ChannelHealthDetail]:
    if isinstance(result, ChannelHealthDetailFailure):
        return _unavailable(result.code, result.retryable)
    return DetailSection(status="loaded", data=result.detail)


def _event_section(result: EventLogPage | EventLogFailure | None) -> DetailSection[tuple[EventLogEntry, ...]]:
    if result is None:
        return _unavailable("event_log_not_configured", True)
    if isinstance(result, EventLogFailure):
        return _unavailable(result.code, result.retryable)
    return DetailSection(status="loaded", data=result.events)


def _unavailable(code: object, retryable: bool) -> DetailSection[SectionValue]:
    return DetailSection(
        status="unavailable",
        failure=DetailSectionFailure(code=str(code), retryable=retryable),
    )
