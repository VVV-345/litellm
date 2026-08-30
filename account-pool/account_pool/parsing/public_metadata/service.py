"""调度、认领并重试不携带渠道凭证的公开元数据解析任务。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol
from uuid import UUID, uuid4

from pydantic import AwareDatetime

from account_pool.catalog.models import AdministrativeState, ChannelSummary
from account_pool.catalog.query import ChannelCatalogReader
from account_pool.domain.provider_source import ProviderValidationFailureCode, ProviderValidationResult
from account_pool.monitoring.loop import run_worker_loop
from account_pool.monitoring.models import WorkerName
from account_pool.monitoring.registry import NoopWorkerMonitor, WorkerMonitor
from account_pool.operational.models import OperationalEventType
from account_pool.operational.public_metadata import build_public_metadata_task_record
from account_pool.operational.repository import OperationalEventRepository, OperationalWriteSuccess
from account_pool.parsing.public_metadata.models import (
    PublicMetadataChannel,
    PublicMetadataTaskFailureCode,
    PublicMetadataTaskRecord,
    PublicMetadataTaskStatus,
)
from account_pool.parsing.public_metadata.repository import (
    PublicMetadataClaimSuccess,
    PublicMetadataRecoverySuccess,
    PublicMetadataTaskRepository,
    PublicMetadataWriteSuccess,
)
from account_pool.parsing.public_metadata.source import PublicMetadataSourceRegistry
from account_pool.parsing.registry import ParserSelectionRequest
from account_pool.parsing.worker import ParserWorkFailure, ParserWorkRequest, ParserWorkResult

Clock = Callable[[], AwareDatetime]
IdFactory = Callable[[], UUID]
_LOGGER: Final = logging.getLogger(__name__)


class PublicMetadataTaskManager(Protocol):
    async def initialize(self) -> None: ...

    async def run(self) -> None: ...

    async def run_once(self) -> None: ...


class ParserWorkerRunner(Protocol):
    async def run(self, request: ParserWorkRequest) -> ParserWorkResult: ...


def utc_now() -> AwareDatetime:
    return datetime.now(UTC)


class PublicMetadataTaskLoop:
    def __init__(
        self,
        *,
        catalog: ChannelCatalogReader,
        sources: PublicMetadataSourceRegistry,
        repository: PublicMetadataTaskRepository,
        worker: ParserWorkerRunner,
        operations: OperationalEventRepository,
        interval_seconds: int = 300,
        refresh_interval_seconds: int = 86_400,
        retry_base_seconds: int = 30,
        batch_size: int = 25,
        max_attempts: int = 3,
        stale_after_seconds: int = 120,
        heartbeat_interval_seconds: float = 15,
        instance_id: UUID | None = None,
        clock: Clock = utc_now,
        id_factory: IdFactory = uuid4,
        monitor: WorkerMonitor | None = None,
    ) -> None:
        if min(interval_seconds, refresh_interval_seconds, retry_base_seconds, batch_size, max_attempts) <= 0:
            raise ValueError("public metadata worker settings must be positive")
        self._catalog: Final = catalog
        self._sources: Final = sources
        self._repository: Final = repository
        self._worker: Final = worker
        self._operations: Final = operations
        self._interval_seconds: Final = interval_seconds
        self._refresh_interval_seconds: Final = refresh_interval_seconds
        self._retry_base_seconds: Final = retry_base_seconds
        self._batch_size: Final = batch_size
        self._max_attempts: Final = max_attempts
        self._stale_after_seconds: Final = stale_after_seconds
        self._heartbeat_interval_seconds: Final = heartbeat_interval_seconds
        self._instance_id: Final = instance_id or uuid4()
        self._clock: Final = clock
        self._id_factory: Final = id_factory
        self._monitor: Final = monitor or NoopWorkerMonitor()

    async def initialize(self) -> None:
        now: Final = self._clock()
        recovered: Final = await self._repository.recover_stale(
            stale_before=now - timedelta(seconds=self._stale_after_seconds),
            at=now,
        )
        if not isinstance(recovered, PublicMetadataRecoverySuccess):
            return
        await asyncio.gather(*(self._record_recovery(task) for task in recovered.records))

    async def run(self) -> None:
        await run_worker_loop(
            worker=WorkerName.PUBLIC_METADATA,
            cycle=self.run_once,
            interval_seconds=self._interval_seconds,
            monitor=self._monitor,
            logger=_LOGGER,
            failure_message="Public metadata parser pass failed",
        )

    async def run_once(self) -> None:
        await self._schedule_due()
        await self._process_batch(self._batch_size)

    async def _schedule_due(self) -> None:
        now: Final = self._clock()
        channels: Final = await self._catalog.list_channels()
        eligible: Final = tuple(
            channel
            for channel in channels.channels
            if channel.administrative_state == AdministrativeState.ENABLED
            and channel.parser_provider_id is not None
            and self._sources.resolve(channel.parser_provider_id) is not None
        )
        records: Final = tuple(
            self._queued_record(channel, channel.parser_provider_id, now)
            for channel in eligible
            if channel.parser_provider_id is not None
        )
        await asyncio.gather(
            *(
                self._repository.schedule(
                    record,
                    refresh_after=now - timedelta(seconds=self._refresh_interval_seconds),
                )
                for record in records
            )
        )

    async def _process_batch(self, remaining: int) -> None:
        if remaining <= 0:
            return
        claimed: Final = await self._repository.claim_next(owner_instance_id=self._instance_id, at=self._clock())
        if not isinstance(claimed, PublicMetadataClaimSuccess) or claimed.record is None:
            return
        await self._execute(claimed.record)
        await self._process_batch(remaining - 1)

    async def _execute(self, task: PublicMetadataTaskRecord) -> None:
        heartbeat: Final = asyncio.create_task(self._heartbeat(task.task_id))
        try:
            channel: Final = await self._channel(task)
            if channel is None:
                await self._finish_failed(task, PublicMetadataTaskFailureCode.CHANNEL_UNAVAILABLE)
                return
            source: Final = self._sources.resolve(channel.provider_id)
            if source is None:
                await self._finish_failed(task, PublicMetadataTaskFailureCode.SOURCE_UNAVAILABLE)
                return
            validation: Final = await source.fetch(channel)
            if validation.key_fingerprint is not None:
                await self._finish_failed(task, PublicMetadataTaskFailureCode.UNSAFE_SOURCE_RESULT)
                return
            failure_code: Final = _source_failure_code(validation)
            if failure_code is not None and _source_retryable(validation) and task.attempt_count < task.max_attempts:
                await self._retry(task, failure_code)
                return
            outcome: Final = await self._worker.run(
                ParserWorkRequest(
                    channel_id=task.channel_id,
                    parser_run_id=task.parser_run_id,
                    parsed_at=task.updated_at,
                    selection=ParserSelectionRequest(
                        provider_id=task.provider_id,
                        api_base=channel.api_base,
                        explicit_parser_id=source.parser_id,
                    ),
                    validation=validation,
                )
            )
            if isinstance(outcome, ParserWorkFailure):
                worker_failure: Final = _worker_failure_code(outcome)
                if outcome.failure.retryable and task.attempt_count < task.max_attempts:
                    await self._retry(task, worker_failure)
                    return
                await self._finish_failed(task, worker_failure)
                return
            if failure_code is not None:
                await self._finish_failed(task, failure_code)
                return
            await self._finish_completed(task)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._retry_or_fail(task, PublicMetadataTaskFailureCode.INTERNAL)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _channel(self, task: PublicMetadataTaskRecord) -> PublicMetadataChannel | None:
        channels: Final = await self._catalog.list_channels()
        matching: Final = tuple(channel for channel in channels.channels if channel.channel_id == task.channel_id)
        if len(matching) != 1:
            return None
        channel: Final = matching[0]
        if (
            channel.administrative_state != AdministrativeState.ENABLED
            or channel.parser_provider_id != task.provider_id
        ):
            return None
        return PublicMetadataChannel(
            channel_id=channel.channel_id,
            provider_id=task.provider_id,
            api_base=channel.base_url_display,
            group=channel.group,
        )

    def _queued_record(
        self,
        channel: ChannelSummary,
        parser_provider_id: str,
        now: AwareDatetime,
    ) -> PublicMetadataTaskRecord:
        return PublicMetadataTaskRecord(
            task_id=self._id_factory(),
            channel_id=channel.channel_id,
            parser_run_id=self._id_factory(),
            provider_id=parser_provider_id,
            status=PublicMetadataTaskStatus.QUEUED,
            attempt_count=0,
            max_attempts=self._max_attempts,
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )

    async def _retry_or_fail(
        self,
        task: PublicMetadataTaskRecord,
        failure_code: PublicMetadataTaskFailureCode,
    ) -> None:
        if task.attempt_count < task.max_attempts:
            await self._retry(task, failure_code)
            return
        await self._finish_failed(task, failure_code)

    async def _retry(
        self,
        task: PublicMetadataTaskRecord,
        failure_code: PublicMetadataTaskFailureCode,
    ) -> None:
        now: Final = self._clock()
        next_attempt_at: Final = now + timedelta(seconds=self._retry_base_seconds * (1 << (task.attempt_count - 1)))
        updated: Final = await self._repository.retry(
            task_id=task.task_id,
            owner_instance_id=self._instance_id,
            parser_run_id=self._id_factory(),
            failure_code=failure_code,
            next_attempt_at=next_attempt_at,
            at=now,
        )
        if isinstance(updated, PublicMetadataWriteSuccess):
            await self._record(
                updated.record,
                OperationalEventType.PUBLIC_METADATA_TASK_RETRY_SCHEDULED,
                failure_code=failure_code,
                next_attempt_at=next_attempt_at,
            )

    async def _finish_completed(self, task: PublicMetadataTaskRecord) -> None:
        now: Final = self._clock()
        updated: Final = await self._repository.finish(
            task_id=task.task_id,
            owner_instance_id=self._instance_id,
            status=PublicMetadataTaskStatus.COMPLETED,
            failure_code=None,
            at=now,
        )
        if isinstance(updated, PublicMetadataWriteSuccess):
            await self._record(updated.record, OperationalEventType.PUBLIC_METADATA_TASK_COMPLETED)

    async def _finish_failed(
        self,
        task: PublicMetadataTaskRecord,
        failure_code: PublicMetadataTaskFailureCode,
    ) -> None:
        now: Final = self._clock()
        updated: Final = await self._repository.finish(
            task_id=task.task_id,
            owner_instance_id=self._instance_id,
            status=PublicMetadataTaskStatus.FAILED,
            failure_code=failure_code,
            at=now,
        )
        if isinstance(updated, PublicMetadataWriteSuccess):
            await self._record(
                updated.record,
                OperationalEventType.PUBLIC_METADATA_TASK_FAILED,
                failure_code=failure_code,
            )

    async def _record_recovery(self, task: PublicMetadataTaskRecord) -> None:
        event_type: Final = (
            OperationalEventType.PUBLIC_METADATA_TASK_FAILED
            if task.status == PublicMetadataTaskStatus.FAILED
            else OperationalEventType.PUBLIC_METADATA_TASK_RETRY_SCHEDULED
        )
        await self._record(
            task,
            event_type,
            failure_code=PublicMetadataTaskFailureCode.WORKER_LOST,
            next_attempt_at=(
                task.next_attempt_at
                if event_type == OperationalEventType.PUBLIC_METADATA_TASK_RETRY_SCHEDULED
                else None
            ),
        )

    async def _record(
        self,
        task: PublicMetadataTaskRecord,
        event_type: OperationalEventType,
        *,
        failure_code: PublicMetadataTaskFailureCode | None = None,
        next_attempt_at: AwareDatetime | None = None,
    ) -> None:
        result: Final = await self._operations.append(
            build_public_metadata_task_record(
                task_id=task.task_id,
                channel_id=task.channel_id,
                parser_run_id=task.parser_run_id,
                provider_id=task.provider_id,
                attempt_count=task.attempt_count,
                occurred_at=self._clock(),
                event_type=event_type,
                failure_code=None if failure_code is None else failure_code.value,
                next_attempt_at=next_attempt_at,
            )
        )
        if not isinstance(result, OperationalWriteSuccess):
            _LOGGER.error("Failed to persist public metadata task event: %s", result.code)

    async def _heartbeat(self, task_id: UUID) -> None:
        while await self._wait_and_heartbeat(task_id):
            pass

    async def _wait_and_heartbeat(self, task_id: UUID) -> bool:
        await asyncio.sleep(self._heartbeat_interval_seconds)
        updated: Final = await self._repository.heartbeat(
            task_id=task_id,
            owner_instance_id=self._instance_id,
            at=self._clock(),
        )
        return isinstance(updated, PublicMetadataWriteSuccess)


def _source_failure_code(validation: ProviderValidationResult) -> PublicMetadataTaskFailureCode | None:
    if validation.ok:
        return None
    if validation.failure_code == ProviderValidationFailureCode.TRANSPORT:
        return PublicMetadataTaskFailureCode.SOURCE_TRANSPORT
    if validation.failure_code in (
        ProviderValidationFailureCode.UPSTREAM_RESPONSE,
        ProviderValidationFailureCode.NO_MODELS,
    ):
        return PublicMetadataTaskFailureCode.SOURCE_INVALID_RESPONSE
    return PublicMetadataTaskFailureCode.SOURCE_UNAVAILABLE


def _source_retryable(validation: ProviderValidationResult) -> bool:
    return validation.failure_code == ProviderValidationFailureCode.TRANSPORT


def _worker_failure_code(failure: ParserWorkFailure) -> PublicMetadataTaskFailureCode:
    if failure.stage == "persistence":
        return PublicMetadataTaskFailureCode.WORKER_PERSISTENCE
    if failure.stage == "overrides":
        return PublicMetadataTaskFailureCode.WORKER_OVERRIDES
    return PublicMetadataTaskFailureCode.WORKER_EXPORT_STATE
