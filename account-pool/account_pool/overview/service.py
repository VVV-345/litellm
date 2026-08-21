"""从目录、解析、健康活动和调度运行态生成渠道聚合总览。"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime
from typing import Final, Protocol
from uuid import UUID

import psycopg
from redis.exceptions import RedisError

from account_pool.catalog.models import AdministrativeState, ChannelList, ChannelSummary
from account_pool.catalog.query import ChannelCatalogReader
from account_pool.health.models import HealthActivity
from account_pool.health.repository import HealthActivityLoadResult, HealthActivityLoadSuccess
from account_pool.models import AccountConfig, AccountSnapshot, Health, RouteEntry
from account_pool.overview.models import (
    AccountPoolOverview,
    AccountPoolOverviewFailure,
    AccountPoolOverviewResult,
    ChannelActivityOverview,
    ChannelOverview,
    MeteredOverview,
    OverviewFailureCode,
    ParserOverview,
    ParserOverviewState,
    RuntimeOverview,
    SubscriptionOverview,
)
from account_pool.parsing.models import MeteredData, SubscriptionData
from account_pool.parsing.service import (
    EffectiveParserData,
    EffectiveParserDataResult,
    ParserDataFailure,
    ParserDataFailureCode,
)


class OverviewRuntimeSource(Protocol):
    def account_configs(self) -> tuple[AccountConfig, ...]: ...

    async def account_snapshots(self) -> tuple[AccountSnapshot, ...]: ...

    def models(self) -> tuple[str, ...]: ...

    async def route_table(self, model: str) -> tuple[RouteEntry, ...]: ...


class OverviewParserSource(Protocol):
    async def effective_data(self, channel_id: UUID) -> EffectiveParserDataResult: ...


class OverviewActivitySource(Protocol):
    async def load_activity(self) -> HealthActivityLoadResult: ...


class AccountPoolOverviewReader(Protocol):
    async def read(self) -> AccountPoolOverviewResult: ...


class AccountPoolOverviewService:
    def __init__(
        self,
        *,
        catalog: ChannelCatalogReader,
        runtime: OverviewRuntimeSource,
        parser_data: OverviewParserSource | None,
        health_events: OverviewActivitySource | None,
    ) -> None:
        self._catalog: Final = catalog
        self._runtime: Final = runtime
        self._parser_data: Final = parser_data
        self._health_events: Final = health_events

    async def read(self) -> AccountPoolOverviewResult:
        try:
            catalog, snapshots, route_groups, activities = await asyncio.gather(
                self._catalog.list_channels(),
                self._runtime.account_snapshots(),
                self._route_groups(),
                self._activities(),
            )
        except (psycopg.Error, RedisError):
            return AccountPoolOverviewFailure(code=OverviewFailureCode.DEPENDENCY_UNAVAILABLE, retryable=True)
        parser_results: Final = await self._parser_results(catalog)
        configs_by_channel: Final = {
            config.channel_id: config for config in self._runtime.account_configs() if config.channel_id is not None
        }
        snapshots_by_account: Final = {snapshot.account_id: snapshot for snapshot in snapshots}
        routes_by_account: Final = _route_groups_by_account(route_groups)
        activity_by_account: Final = _activity_groups(activities)
        channels: Final = tuple(
            _channel_overview(
                channel=channel,
                config=configs_by_channel.get(channel.channel_id),
                snapshot=_snapshot_for_channel(channel.channel_id, configs_by_channel, snapshots_by_account),
                routes=routes_by_account,
                parser=parser_results[channel.channel_id],
                activities=activity_by_account,
                activity_persistence_available=self._health_events is not None and activities is not None,
            )
            for channel in catalog.channels
        )
        configured_models: Final = frozenset(model for channel in channels for model in channel.configured_models)
        schedulable_models: Final = frozenset(model for channel in channels for model in channel.schedulable_models)
        return AccountPoolOverview(
            channels=channels,
            channel_count=len(channels),
            administratively_enabled_count=sum(
                channel.administrative_state == AdministrativeState.ENABLED for channel in channels
            ),
            healthy_count=sum(channel.runtime is not None and channel.runtime.health == Health.HEALTHY for channel in channels),
            schedulable_count=sum(bool(channel.schedulable_models) for channel in channels),
            configured_model_count=len(configured_models),
            schedulable_model_count=len(schedulable_models),
            inflight=sum(channel.runtime.inflight for channel in channels if channel.runtime is not None),
            max_concurrency=sum(channel.runtime.max_concurrency for channel in channels if channel.runtime is not None),
        )

    async def _route_groups(self) -> tuple[tuple[RouteEntry, ...], ...]:
        models: Final = self._runtime.models()
        if not models:
            return ()
        return tuple(await asyncio.gather(*(self._runtime.route_table(model) for model in models)))

    async def _parser_results(self, catalog: ChannelList) -> dict[UUID, ParserOverview]:
        if self._parser_data is None:
            unavailable: Final = ParserOverview(state=ParserOverviewState.UNAVAILABLE, failure_code="not_configured")
            return {channel.channel_id: unavailable for channel in catalog.channels}
        results: Final = await asyncio.gather(
            *(self._parser_data.effective_data(channel.channel_id) for channel in catalog.channels)
        )
        return {
            channel.channel_id: _parser_overview(result)
            for channel, result in zip(catalog.channels, results, strict=True)
        }

    async def _activities(self) -> tuple[HealthActivity, ...] | None:
        if self._health_events is None:
            return None
        result: Final = await self._health_events.load_activity()
        return result.activities if isinstance(result, HealthActivityLoadSuccess) else None


def _snapshot_for_channel(
    channel_id: UUID,
    configs_by_channel: dict[UUID, AccountConfig],
    snapshots_by_account: dict[str, AccountSnapshot],
) -> AccountSnapshot | None:
    config: Final = configs_by_channel.get(channel_id)
    return None if config is None else snapshots_by_account.get(config.id)


def _route_groups_by_account(route_groups: tuple[tuple[RouteEntry, ...], ...]) -> dict[str, tuple[RouteEntry, ...]]:
    account_ids: Final = frozenset(route.account_id for routes in route_groups for route in routes)
    return {
        account_id: tuple(route for routes in route_groups for route in routes if route.account_id == account_id)
        for account_id in account_ids
    }


def _activity_groups(activities: tuple[HealthActivity, ...] | None) -> dict[str, tuple[HealthActivity, ...]]:
    if activities is None:
        return {}
    account_ids: Final = frozenset(activity.account_id for activity in activities)
    return {
        account_id: tuple(activity for activity in activities if activity.account_id == account_id)
        for account_id in account_ids
    }


def _channel_overview(
    *,
    channel: ChannelSummary,
    config: AccountConfig | None,
    snapshot: AccountSnapshot | None,
    routes: dict[str, tuple[RouteEntry, ...]],
    parser: ParserOverview,
    activities: dict[str, tuple[HealthActivity, ...]],
    activity_persistence_available: bool,
) -> ChannelOverview:
    account_id: Final = None if config is None else config.id
    activity: Final = () if account_id is None else activities.get(account_id, ())
    account_routes: Final = () if account_id is None else routes.get(account_id, ())
    schedulable_models: Final = tuple(sorted(frozenset(route.public_model for route in account_routes if route.available)))
    return ChannelOverview(
        channel_id=channel.channel_id,
        account_id=account_id,
        display_name=channel.display_name,
        provider=channel.provider,
        group=channel.group,
        base_url_display=channel.base_url_display,
        key_mask=channel.key_mask,
        administrative_state=channel.administrative_state,
        priority=channel.priority,
        configured_models=channel.models,
        schedulable_models=schedulable_models,
        unavailable_reason_codes=_unavailable_reason_codes(
            channel=channel,
            config=config,
            snapshot=snapshot,
            routes=account_routes,
            schedulable_models=schedulable_models,
        ),
        binding_count=channel.binding_count,
        enabled_binding_count=channel.enabled_binding_count,
        runtime=None if snapshot is None else _runtime_overview(snapshot),
        parser=parser,
        activity=_activity_overview(activity, activity_persistence_available),
    )


def _unavailable_reason_codes(
    *,
    channel: ChannelSummary,
    config: AccountConfig | None,
    snapshot: AccountSnapshot | None,
    routes: tuple[RouteEntry, ...],
    schedulable_models: tuple[str, ...],
) -> tuple[str, ...]:
    route_reasons: Final = tuple(
        dict.fromkeys(
            reason
            for route in routes
            if not route.available
            for reason in (route.reason_code or route.unavailable_reason,)
            if reason is not None
        )
    )
    if schedulable_models or route_reasons:
        return route_reasons
    if channel.administrative_state != AdministrativeState.ENABLED:
        return (channel.administrative_state.value,)
    if not channel.models:
        return ("no_model_bindings",)
    if config is None:
        return ("runtime_not_projected",)
    if snapshot is None:
        return ("runtime_state_unavailable",)
    if snapshot.reason_code is not None:
        return (snapshot.reason_code,)
    if not routes:
        return ("routing_not_projected",)
    return ("no_available_route",)


def _runtime_overview(snapshot: AccountSnapshot) -> RuntimeOverview:
    return RuntimeOverview(
        health=snapshot.health,
        reason_code=snapshot.reason_code,
        inflight=snapshot.inflight,
        max_concurrency=snapshot.max_concurrency,
        cooldown_until=snapshot.cooldown_until,
        quota=snapshot.quota,
    )


def _parser_overview(result: EffectiveParserData | ParserDataFailure) -> ParserOverview:
    if isinstance(result, ParserDataFailure):
        state: Final = (
            ParserOverviewState.NOT_RUN
            if result.code == ParserDataFailureCode.RUN_NOT_FOUND
            else ParserOverviewState.UNAVAILABLE
            if result.code == ParserDataFailureCode.DATABASE_UNAVAILABLE
            else ParserOverviewState.INVALID
        )
        return ParserOverview(state=state, failure_code=result.code)
    parsed: Final = result.effective_result
    return ParserOverview(
        state=ParserOverviewState.LOADED,
        parser_id=result.parser_id,
        parser_version=result.parser_version,
        status=result.parser_status,
        parsed_at=result.parsed_at,
        subscription=_subscription_overview(parsed.subscription),
        metered=_metered_overview(parsed.metered),
        unresolved_count=len(parsed.unresolved_fields),
        warning_count=len(parsed.warnings),
        active_override_count=len(result.active_overrides),
    )


def _subscription_overview(subscription: SubscriptionData | None) -> SubscriptionOverview | None:
    if subscription is None:
        return None
    return SubscriptionOverview(
        plan_name=subscription.plan_name,
        status=subscription.status,
        expires_at=subscription.expires_at,
        balance=subscription.balance,
        currency=subscription.currency,
        model_count=len(subscription.models),
        limit_count=len(subscription.limits),
    )


def _metered_overview(metered: MeteredData | None) -> MeteredOverview | None:
    if metered is None:
        return None
    return MeteredOverview(
        group_count=len(metered.groups),
        model_count=sum(len(group.models) for group in metered.groups),
    )


def _activity_overview(
    activities: tuple[HealthActivity, ...],
    persistence_available: bool,
) -> ChannelActivityOverview:
    return ChannelActivityOverview(
        persistence_available=persistence_available,
        last_request_at=_latest(activity.last_request_at for activity in activities),
        last_success_at=_latest(activity.last_success_at for activity in activities),
        last_failure_at=_latest(activity.last_failure_at for activity in activities),
        last_probe_at=_latest(activity.last_probe_at for activity in activities),
    )


def _latest(values: Iterable[datetime | None]) -> datetime | None:
    dated: Final = tuple(value for value in values if value is not None)
    return max(dated, default=None)
