"""组合运行时健康状态、资格排除和 PostgreSQL 健康事实，形成渠道详情查询。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Final, Literal, Protocol
from uuid import UUID

from account_pool.eligibility import EligibilityExclusion, EligibilityScope, settlement_exclusion
from account_pool.health.models import (
    HealthActivity,
    HealthEventRecord,
    HealthProbeResult,
    HealthRequestActivity,
    build_active_probe_record,
    build_passive_health_record,
)
from account_pool.health.repository import (
    HealthActivityLoadSuccess,
    HealthEventListSuccess,
    HealthEventRepository,
    HealthPersistenceFailure,
)
from account_pool.health.settlement import classify_settlement
from account_pool.models import AccountConfig, AccountSnapshot, FrozenModel, Lease, SettleRequest
from account_pool.store import StateStore

_LOGGER: Final = logging.getLogger(__name__)


class HealthAccountSource(Protocol):
    def account_configs(self) -> tuple[AccountConfig, ...]: ...


class HealthEventRecorder:
    def __init__(self, *, accounts: HealthAccountSource, events: HealthEventRepository) -> None:
        self._accounts: Final = accounts
        self._events: Final = events

    async def record_request(self, lease: Lease) -> bool:
        account: Final = self._account(lease.account_id)
        if account is None:
            return False
        result: Final = await self._events.record_request(
            HealthRequestActivity(
                channel_id=account.channel_id,
                account_id=lease.account_id,
                model_id=lease.public_model,
                deployment_id=lease.deployment_id,
                observed_at=datetime.now(UTC),
            )
        )
        if isinstance(result, HealthPersistenceFailure):
            _LOGGER.error("Failed to persist Account Pool request activity: %s", result.code)
            return False
        return True

    async def record_passive(self, lease: Lease, request: SettleRequest) -> bool:
        account: Final = self._account(lease.account_id)
        if account is None:
            return False
        occurred_at: Final = datetime.now(UTC)
        transition: Final = classify_settlement(request, occurred_at.timestamp())
        exclusion: Final = settlement_exclusion(lease=lease, transition=transition, now=occurred_at.timestamp())
        result: Final = await self._events.append(
            build_passive_health_record(
                account=account,
                lease=lease,
                request=request,
                occurred_at=occurred_at,
                scope=EligibilityScope.DEPLOYMENT if exclusion is None else exclusion.scope,
            )
        )
        if isinstance(result, HealthPersistenceFailure):
            _LOGGER.error("Failed to persist Account Pool passive health event: %s", result.code)
            return False
        return True

    async def record_probe(self, result: HealthProbeResult, settlement: SettleRequest) -> bool:
        occurred_at: Final = datetime.now(UTC)
        record: Final = build_active_probe_record(
            result=result,
            transition=classify_settlement(settlement, occurred_at.timestamp()),
            occurred_at=occurred_at,
        )
        if record is None:
            return False
        written: Final = await self._events.append(record)
        if isinstance(written, HealthPersistenceFailure):
            _LOGGER.error("Failed to persist Account Pool active health event: %s", written.code)
            return False
        return True

    def _account(self, account_id: str) -> AccountConfig | None:
        return next((candidate for candidate in self._accounts.account_configs() if candidate.id == account_id), None)


class ChannelHealthDetail(FrozenModel):
    channel_id: UUID
    account_id: str
    runtime: AccountSnapshot
    exclusions: tuple[EligibilityExclusion, ...]
    activities: tuple[HealthActivity, ...]
    events: tuple[HealthEventRecord, ...]
    persistence_available: bool


class ChannelHealthDetailSuccess(FrozenModel):
    status: Literal["loaded"] = "loaded"
    detail: ChannelHealthDetail


class ChannelHealthDetailFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: Literal["channel_not_found", "runtime_unavailable", "database_unavailable"]
    retryable: bool


ChannelHealthDetailResult = ChannelHealthDetailSuccess | ChannelHealthDetailFailure


class ChannelHealthDetailReader(Protocol):
    async def read_channel(self, channel_id: UUID) -> ChannelHealthDetailResult: ...


class ChannelHealthQueryService:
    def __init__(
        self,
        *,
        accounts: HealthAccountSource,
        store: StateStore,
        events: HealthEventRepository | None,
    ) -> None:
        self._accounts: Final = accounts
        self._store: Final = store
        self._events: Final = events

    async def read_channel(self, channel_id: UUID) -> ChannelHealthDetailResult:
        account: Final = next(
            (candidate for candidate in self._accounts.account_configs() if candidate.channel_id == channel_id),
            None,
        )
        if account is None:
            return ChannelHealthDetailFailure(code="channel_not_found", retryable=False)
        runtime: Final = next(
            (snapshot for snapshot in await self._store.snapshots() if snapshot.account_id == account.id),
            None,
        )
        if runtime is None:
            return ChannelHealthDetailFailure(code="runtime_unavailable", retryable=True)
        exclusions: Final = tuple(
            exclusion
            for exclusion in await self._store.eligibility_exclusions()
            if exclusion.account_id == account.id
        )
        if self._events is None:
            return ChannelHealthDetailSuccess(
                detail=ChannelHealthDetail(
                    channel_id=channel_id,
                    account_id=account.id,
                    runtime=runtime,
                    exclusions=exclusions,
                    activities=(),
                    events=(),
                    persistence_available=False,
                )
            )
        activity_result: Final = await self._events.load_activity()
        event_result: Final = await self._events.list_recent(channel_id)
        if not isinstance(activity_result, HealthActivityLoadSuccess) or not isinstance(
            event_result, HealthEventListSuccess
        ):
            return ChannelHealthDetailFailure(code="database_unavailable", retryable=True)
        return ChannelHealthDetailSuccess(
            detail=ChannelHealthDetail(
                channel_id=channel_id,
                account_id=account.id,
                runtime=runtime,
                exclusions=exclusions,
                activities=tuple(item for item in activity_result.activities if item.account_id == account.id),
                events=event_result.records,
                persistence_available=True,
            )
        )
