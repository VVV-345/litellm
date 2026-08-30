"""验证公开元数据任务的调度、重试、恢复和敏感信息隔离。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from account_pool.catalog.models import AdministrativeState, ChannelList, ChannelSummary
from account_pool.domain.provider_source import (
    ModelOffer,
    ProviderValidationFailureCode,
    ProviderValidationResult,
)
from account_pool.models import ChannelPriority
from account_pool.operational.models import OperationalEventRecord, OperationalEventType
from account_pool.operational.public_metadata_models import PublicMetadataTaskRetryScheduledDetails
from account_pool.operational.repository import OperationalWriteResult, OperationalWriteSuccess
from account_pool.parsing.models import ParsedChannelData, ParserRun, ParserRunStatus
from account_pool.parsing.persistence import ParserExportState, ParserExportStatus
from account_pool.parsing.public_metadata.models import (
    PublicMetadataChannel,
    PublicMetadataTaskFailureCode,
    PublicMetadataTaskRecord,
    PublicMetadataTaskStatus,
)
from account_pool.parsing.public_metadata.repository import (
    PublicMetadataClaimResult,
    PublicMetadataClaimSuccess,
    PublicMetadataRecoveryResult,
    PublicMetadataRecoverySuccess,
    PublicMetadataScheduleResult,
    PublicMetadataScheduleSuccess,
    PublicMetadataWriteResult,
    PublicMetadataWriteSuccess,
)
from account_pool.parsing.public_metadata.service import PublicMetadataTaskLoop
from account_pool.parsing.public_metadata.source import (
    PublicMetadataSourceRegistry,
    RegisteredPublicMetadataSource,
)
from account_pool.parsing.worker import ParserWorkRequest, ParserWorkResult, ParserWorkSuccess

_CHANNEL_ID: Final = UUID("11000000-0000-0000-0000-000000000001")
_TASK_ID: Final = UUID("22000000-0000-0000-0000-000000000002")
_RUN_ID: Final = UUID("33000000-0000-0000-0000-000000000003")
_RETRY_RUN_ID: Final = UUID("44000000-0000-0000-0000-000000000004")
_INSTANCE_ID: Final = UUID("55000000-0000-0000-0000-000000000005")
_NOW: Final = datetime(2026, 8, 22, 10, 0, tzinfo=UTC)
_API_BASE: Final = "https://metadata.example.test/v1"


class FakeCatalog:
    def __init__(
        self,
        state: AdministrativeState = AdministrativeState.ENABLED,
        provider: str = "upstream_fixture",
        parser_provider_id: str | None = "public_fixture",
    ) -> None:
        self._state: Final = state
        self._provider: Final = provider
        self._parser_provider_id: Final = parser_provider_id

    async def list_channels(self) -> ChannelList:
        return ChannelList(channels=(_channel(self._state, self._provider, self._parser_provider_id),))

    async def get_channel(self, channel_id: UUID) -> ChannelSummary | None:
        channel: Final = _channel(self._state, self._provider, self._parser_provider_id)
        return channel if channel.channel_id == channel_id else None


class FakeRepository:
    def __init__(self, recovered: tuple[PublicMetadataTaskRecord, ...] = ()) -> None:
        self.records: tuple[PublicMetadataTaskRecord, ...] = ()
        self.recovered: Final = recovered
        self.scheduled_refresh_after: datetime | None = None

    async def schedule(
        self,
        record: PublicMetadataTaskRecord,
        refresh_after: datetime,
    ) -> PublicMetadataScheduleResult:
        self.scheduled_refresh_after = refresh_after
        if self.records:
            return PublicMetadataScheduleSuccess(status="unchanged")
        self.records = (record,)
        return PublicMetadataScheduleSuccess(status="created", record=record)

    async def recover_stale(self, stale_before: datetime, at: datetime) -> PublicMetadataRecoveryResult:
        del stale_before, at
        return PublicMetadataRecoverySuccess(records=self.recovered)

    async def claim_next(self, owner_instance_id: UUID, at: datetime) -> PublicMetadataClaimResult:
        claimable: Final = tuple(
            record
            for record in self.records
            if record.status in (PublicMetadataTaskStatus.QUEUED, PublicMetadataTaskStatus.RETRY_WAIT)
            and record.next_attempt_at <= at
        )
        if not claimable:
            return PublicMetadataClaimSuccess(status="empty")
        record: Final = claimable[0]
        updated: Final = record.model_copy(
            update={
                "status": PublicMetadataTaskStatus.RUNNING,
                "attempt_count": record.attempt_count + 1,
                "owner_instance_id": owner_instance_id,
                "started_at": record.started_at or at,
                "updated_at": at,
                "failure_code": None,
            }
        )
        self._replace(updated)
        return PublicMetadataClaimSuccess(status="claimed", record=updated)

    async def heartbeat(
        self,
        task_id: UUID,
        owner_instance_id: UUID,
        at: datetime,
    ) -> PublicMetadataWriteResult:
        record: Final = self._owned(task_id, owner_instance_id)
        updated: Final = record.model_copy(update={"updated_at": at})
        self._replace(updated)
        return PublicMetadataWriteSuccess(record=updated)

    async def retry(
        self,
        task_id: UUID,
        owner_instance_id: UUID,
        parser_run_id: UUID,
        failure_code: PublicMetadataTaskFailureCode,
        next_attempt_at: datetime,
        at: datetime,
    ) -> PublicMetadataWriteResult:
        record: Final = self._owned(task_id, owner_instance_id)
        updated: Final = record.model_copy(
            update={
                "status": PublicMetadataTaskStatus.RETRY_WAIT,
                "parser_run_id": parser_run_id,
                "owner_instance_id": None,
                "next_attempt_at": next_attempt_at,
                "updated_at": at,
                "failure_code": failure_code,
            }
        )
        self._replace(updated)
        return PublicMetadataWriteSuccess(record=updated)

    async def finish(
        self,
        task_id: UUID,
        owner_instance_id: UUID,
        status: PublicMetadataTaskStatus,
        failure_code: PublicMetadataTaskFailureCode | None,
        at: datetime,
    ) -> PublicMetadataWriteResult:
        record: Final = self._owned(task_id, owner_instance_id)
        updated: Final = record.model_copy(
            update={
                "status": status,
                "owner_instance_id": None,
                "updated_at": at,
                "completed_at": at,
                "failure_code": failure_code,
            }
        )
        self._replace(updated)
        return PublicMetadataWriteSuccess(record=updated)

    def _owned(self, task_id: UUID, owner_instance_id: UUID) -> PublicMetadataTaskRecord:
        record: Final = next(record for record in self.records if record.task_id == task_id)
        assert record.status == PublicMetadataTaskStatus.RUNNING
        assert record.owner_instance_id == owner_instance_id
        return record

    def _replace(self, updated: PublicMetadataTaskRecord) -> None:
        self.records = tuple(updated if record.task_id == updated.task_id else record for record in self.records)


class FakeWorker:
    def __init__(self) -> None:
        self.requests: tuple[ParserWorkRequest, ...] = ()

    async def run(self, request: ParserWorkRequest) -> ParserWorkResult:
        self.requests = (*self.requests, request)
        run: Final = ParserRun(
            parser_run_id=request.parser_run_id,
            channel_id=request.channel_id,
            parser_id="public-fixture",
            parser_version="1.0.0",
            parsed_at=request.parsed_at,
            status=ParserRunStatus.PARTIAL,
            result=ParsedChannelData(warnings=("公开元数据已解析",)),
        )
        return ParserWorkSuccess(
            status="exported",
            run=run,
            persistence_status="created",
            export=ParserExportState(
                status=ParserExportStatus.SUCCEEDED,
                attempt_count=1,
                last_attempt_at=request.parsed_at,
                exported_at=request.parsed_at,
            ),
        )


class FakeOperations:
    def __init__(self) -> None:
        self.records: tuple[OperationalEventRecord, ...] = ()

    async def append(self, record: OperationalEventRecord) -> OperationalWriteResult:
        self.records = (*self.records, record)
        return OperationalWriteSuccess(status="created", record=record)


class FixedIds:
    def __init__(self, values: tuple[UUID, ...] = (_TASK_ID, _RUN_ID, _RETRY_RUN_ID)) -> None:
        self._values: tuple[UUID, ...] = values

    def __call__(self) -> UUID:
        value, *remaining = self._values
        self._values = tuple(remaining)
        return value


def _channel(
    state: AdministrativeState,
    provider: str,
    parser_provider_id: str | None,
) -> ChannelSummary:
    return ChannelSummary(
        channel_id=_CHANNEL_ID,
        display_name="公开元数据渠道",
        provider=provider,
        parser_provider_id=parser_provider_id,
        group="default",
        base_url_display=_API_BASE,
        administrative_state=state,
        max_concurrency=2,
        priority=ChannelPriority.MEDIUM,
        weight=10,
        binding_count=1,
        enabled_binding_count=1,
        models=("model-a",),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _validation(
    *,
    ok: bool = True,
    failure_code: ProviderValidationFailureCode | None = None,
    key_fingerprint: str | None = None,
) -> ProviderValidationResult:
    return ProviderValidationResult(
        ok=ok,
        provider_id="public_fixture",
        normalized_api_base=_API_BASE,
        group="default",
        key_fingerprint=key_fingerprint,
        message="upstream-body-must-not-be-logged",
        failure_code=failure_code,
        capabilities=(),
        models=(ModelOffer(model="model-a"),) if ok else (),
    )


def _source(
    result: ProviderValidationResult,
) -> tuple[PublicMetadataSourceRegistry, Callable[[PublicMetadataChannel], Awaitable[ProviderValidationResult]]]:
    async def fetch(channel: PublicMetadataChannel) -> ProviderValidationResult:
        assert channel.api_base == _API_BASE
        return result

    return (
        PublicMetadataSourceRegistry(
            (
                RegisteredPublicMetadataSource(
                    provider_ids=("public_fixture",),
                    parser_id="public-fixture",
                    fetch=fetch,
                ),
            )
        ),
        fetch,
    )


def _loop(
    *,
    repository: FakeRepository,
    worker: FakeWorker,
    operations: FakeOperations,
    validation: ProviderValidationResult,
    state: AdministrativeState = AdministrativeState.ENABLED,
    provider: str = "upstream_fixture",
    parser_provider_id: str | None = "public_fixture",
    ids: tuple[UUID, ...] = (_TASK_ID, _RUN_ID, _RETRY_RUN_ID),
    max_attempts: int = 3,
) -> PublicMetadataTaskLoop:
    sources, _ = _source(validation)
    return PublicMetadataTaskLoop(
        catalog=FakeCatalog(state, provider, parser_provider_id),
        sources=sources,
        repository=repository,
        worker=worker,
        operations=operations,
        interval_seconds=300,
        refresh_interval_seconds=86_400,
        retry_base_seconds=30,
        batch_size=1,
        max_attempts=max_attempts,
        heartbeat_interval_seconds=3600,
        instance_id=_INSTANCE_ID,
        clock=lambda: _NOW,
        id_factory=FixedIds(ids),
    )


async def test_successful_public_metadata_task_is_parsed_and_completed() -> None:
    repository: Final = FakeRepository()
    worker: Final = FakeWorker()
    operations: Final = FakeOperations()
    loop: Final = _loop(repository=repository, worker=worker, operations=operations, validation=_validation())

    await loop.run_once()

    assert repository.records[0].status == PublicMetadataTaskStatus.COMPLETED
    assert repository.scheduled_refresh_after == _NOW - timedelta(days=1)
    assert worker.requests[0].selection.explicit_parser_id == "public-fixture"
    assert operations.records[0].event.event_type == OperationalEventType.PUBLIC_METADATA_TASK_COMPLETED


async def test_transport_failure_schedules_exponential_retry_with_new_parser_run() -> None:
    repository: Final = FakeRepository()
    worker: Final = FakeWorker()
    operations: Final = FakeOperations()
    loop: Final = _loop(
        repository=repository,
        worker=worker,
        operations=operations,
        validation=_validation(ok=False, failure_code=ProviderValidationFailureCode.TRANSPORT),
    )

    await loop.run_once()

    record: Final = repository.records[0]
    assert record.status == PublicMetadataTaskStatus.RETRY_WAIT
    assert record.parser_run_id == _RETRY_RUN_ID
    assert record.next_attempt_at == _NOW + timedelta(seconds=30)
    assert worker.requests == ()
    details: Final = operations.records[0].event.safe_details
    assert isinstance(details, PublicMetadataTaskRetryScheduledDetails)
    assert details.parser_run_id == _RETRY_RUN_ID


async def test_last_transport_attempt_is_persisted_then_permanently_failed() -> None:
    repository: Final = FakeRepository()
    worker: Final = FakeWorker()
    operations: Final = FakeOperations()
    loop: Final = _loop(
        repository=repository,
        worker=worker,
        operations=operations,
        validation=_validation(ok=False, failure_code=ProviderValidationFailureCode.TRANSPORT),
        ids=(_TASK_ID, _RUN_ID),
        max_attempts=1,
    )

    await loop.run_once()

    assert repository.records[0].status == PublicMetadataTaskStatus.FAILED
    assert repository.records[0].failure_code == PublicMetadataTaskFailureCode.SOURCE_TRANSPORT
    assert len(worker.requests) == 1
    assert operations.records[0].event.event_type == OperationalEventType.PUBLIC_METADATA_TASK_FAILED


async def test_source_result_with_key_fingerprint_is_rejected_without_parser_execution() -> None:
    repository: Final = FakeRepository()
    worker: Final = FakeWorker()
    operations: Final = FakeOperations()
    loop: Final = _loop(
        repository=repository,
        worker=worker,
        operations=operations,
        validation=_validation(key_fingerprint="secret-fingerprint"),
    )

    await loop.run_once()

    assert repository.records[0].failure_code == PublicMetadataTaskFailureCode.UNSAFE_SOURCE_RESULT
    assert worker.requests == ()
    serialized: Final = operations.records[0].model_dump_json()
    assert _API_BASE not in serialized
    assert "secret-fingerprint" not in serialized
    assert "upstream-body-must-not-be-logged" not in serialized


async def test_disabled_channel_is_not_scheduled() -> None:
    repository: Final = FakeRepository()
    loop: Final = _loop(
        repository=repository,
        worker=FakeWorker(),
        operations=FakeOperations(),
        validation=_validation(),
        state=AdministrativeState.DISABLED,
    )

    await loop.run_once()

    assert repository.records == ()


async def test_upstream_provider_does_not_select_a_public_metadata_parser() -> None:
    repository: Final = FakeRepository()
    loop: Final = _loop(
        repository=repository,
        worker=FakeWorker(),
        operations=FakeOperations(),
        validation=_validation(),
        provider="public_fixture",
        parser_provider_id=None,
    )

    await loop.run_once()

    assert repository.records == ()


async def test_initialize_records_stale_worker_recovery_without_sensitive_fields() -> None:
    stale: Final = PublicMetadataTaskRecord(
        task_id=_TASK_ID,
        channel_id=_CHANNEL_ID,
        parser_run_id=_RUN_ID,
        provider_id="public_fixture",
        status=PublicMetadataTaskStatus.RETRY_WAIT,
        attempt_count=1,
        max_attempts=3,
        next_attempt_at=_NOW,
        created_at=_NOW - timedelta(minutes=5),
        updated_at=_NOW,
        started_at=_NOW - timedelta(minutes=4),
        failure_code=PublicMetadataTaskFailureCode.WORKER_LOST,
    )
    operations: Final = FakeOperations()
    loop: Final = _loop(
        repository=FakeRepository(recovered=(stale,)),
        worker=FakeWorker(),
        operations=operations,
        validation=_validation(),
    )

    await loop.initialize()

    assert operations.records[0].event.event_type == OperationalEventType.PUBLIC_METADATA_TASK_RETRY_SCHEDULED
    serialized: Final = operations.records[0].model_dump_json()
    assert _API_BASE not in serialized
    assert "api_key" not in serialized
    assert "authorization" not in serialized.casefold()
