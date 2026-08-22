"""验证一次性 Key 解析任务的本地接管、持久状态、心跳和中断边界。"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from account_pool.audit.models import ManagementAuditRecord, ManagementEventType
from account_pool.audit.repository import (
    AuditLoadResult,
    AuditPersistenceFailure,
    AuditPersistenceFailureCode,
    AuditWriteResult,
    AuditWriteSuccess,
)
from account_pool.auth.actor import ActorAction, ActorContext
from account_pool.domain.provider_source import ModelOffer, ProviderValidationRequest, ProviderValidationResult
from account_pool.operational.models import (
    OperationalEventRecord,
    OperationalEventType,
    ParserTaskInterruptionSource,
)
from account_pool.operational.repository import OperationalWriteResult, OperationalWriteSuccess
from account_pool.parsing.models import ParsedChannelData, ParserRun, ParserRunStatus
from account_pool.parsing.persistence import ParserExportState, ParserExportStatus
from account_pool.parsing.tasks.models import (
    ParserTaskAccepted,
    ParserTaskFailureCode,
    ParserTaskOperationFailure,
    ParserTaskOperationFailureCode,
    ParserTaskRecord,
    ParserTaskStartRequest,
    ParserTaskStatus,
    ParserTaskView,
)
from account_pool.parsing.tasks.repository import (
    ParserTaskLoadResult,
    ParserTaskLoadSuccess,
    ParserTaskPersistenceFailure,
    ParserTaskPersistenceFailureCode,
    ParserTaskSweepResult,
    ParserTaskSweepSuccess,
    ParserTaskWriteResult,
    ParserTaskWriteSuccess,
)
from account_pool.parsing.tasks.service import ParserTaskService
from account_pool.parsing.worker import ParserWorkRequest, ParserWorkResult, ParserWorkSuccess
from pydantic import SecretStr

_CHANNEL_ID: Final = UUID("10000000-0000-0000-0000-000000000001")
_TASK_ID: Final = UUID("20000000-0000-0000-0000-000000000002")
_RUN_ID: Final = UUID("30000000-0000-0000-0000-000000000003")
_INSTANCE_ID: Final = UUID("40000000-0000-0000-0000-000000000004")
_NOW: Final = datetime(2026, 8, 19, 22, 0, tzinfo=UTC)
_SECRET: Final = "one-time-provider-secret"
_API_BASE: Final = "https://gateway.example.com/v1"


class FakeTaskRepository:
    def __init__(self) -> None:
        self.records: dict[UUID, ParserTaskRecord] = {}
        self.swept_before: datetime | None = None

    async def create(self, record: ParserTaskRecord) -> ParserTaskWriteResult:
        self.records[record.task_id] = record
        return ParserTaskWriteSuccess(status="created", record=record)

    async def load(self, channel_id: UUID, task_id: UUID) -> ParserTaskLoadResult:
        record: Final = self.records.get(task_id)
        if record is None or record.channel_id != channel_id:
            return ParserTaskPersistenceFailure(
                code=ParserTaskPersistenceFailureCode.TASK_NOT_FOUND,
                retryable=False,
            )
        return ParserTaskLoadSuccess(record=record)

    async def heartbeat(self, task_id: UUID, owner_instance_id: UUID, at: datetime) -> ParserTaskWriteResult:
        record: Final = self.records[task_id]
        if record.owner_instance_id != owner_instance_id or record.status != ParserTaskStatus.RUNNING:
            return ParserTaskPersistenceFailure(
                code=ParserTaskPersistenceFailureCode.OWNERSHIP_CONFLICT,
                retryable=False,
            )
        updated: Final = record.model_copy(update={"heartbeat_at": at})
        self.records[task_id] = updated
        return ParserTaskWriteSuccess(status="updated", record=updated)

    async def finish(
        self,
        task_id: UUID,
        owner_instance_id: UUID,
        status: ParserTaskStatus,
        failure_code: ParserTaskFailureCode | None,
        at: datetime,
    ) -> ParserTaskWriteResult:
        record: Final = self.records[task_id]
        if record.owner_instance_id != owner_instance_id or record.status != ParserTaskStatus.RUNNING:
            return ParserTaskPersistenceFailure(
                code=ParserTaskPersistenceFailureCode.OWNERSHIP_CONFLICT,
                retryable=False,
            )
        updated: Final = record.model_copy(
            update={
                "status": status,
                "failure_code": failure_code,
                "completed_at": at,
                "heartbeat_at": at,
            }
        )
        self.records[task_id] = updated
        return ParserTaskWriteSuccess(status="updated", record=updated)

    async def sweep_stale(self, stale_before: datetime, at: datetime) -> ParserTaskSweepResult:
        self.swept_before = stale_before
        interrupted: Final = tuple(
            task_id
            for task_id, record in self.records.items()
            if record.status == ParserTaskStatus.RUNNING and record.heartbeat_at < stale_before
        )
        self.records = {
            task_id: (
                record.model_copy(
                    update={
                        "status": ParserTaskStatus.INTERRUPTED_REQUIRES_KEY,
                        "completed_at": at,
                        "heartbeat_at": at,
                    }
                )
                if task_id in interrupted
                else record
            )
            for task_id, record in self.records.items()
        }
        return ParserTaskSweepSuccess(interrupted_tasks=tuple(self.records[task_id] for task_id in interrupted))


class FakeAuditRepository:
    def __init__(self) -> None:
        self.records: tuple[ManagementAuditRecord, ...] = ()

    async def append(self, record: ManagementAuditRecord) -> AuditWriteResult:
        self.records = (*self.records, record)
        return AuditWriteSuccess(status="created", record=record)

    async def load(self, event_id: UUID) -> AuditLoadResult:
        raise AssertionError(f"parser task service must not load audit events: {event_id}")


class FailingAuditRepository:
    async def append(self, record: ManagementAuditRecord) -> AuditWriteResult:
        return AuditPersistenceFailure(code=AuditPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def load(self, event_id: UUID) -> AuditLoadResult:
        raise AssertionError(f"parser task service must not load audit events: {event_id}")


class FakeOperationalRepository:
    def __init__(self) -> None:
        self.records: tuple[OperationalEventRecord, ...] = ()

    async def append(self, record: OperationalEventRecord) -> OperationalWriteResult:
        self.records = (*self.records, record)
        return OperationalWriteSuccess(status="created", record=record)


class GatedProvider:
    def __init__(self) -> None:
        self.started: Final = asyncio.Event()
        self.release: Final = asyncio.Event()
        self.request: ProviderValidationRequest | None = None

    async def validate(self, request: ProviderValidationRequest) -> ProviderValidationResult:
        self.request = request
        self.started.set()
        await self.release.wait()
        return ProviderValidationResult(
            ok=True,
            provider_id=request.provider_id,
            normalized_api_base=request.api_base,
            group=request.group,
            key_fingerprint=None,
            message="ok",
            capabilities=(),
            models=(ModelOffer(model="model-a"),),
        )


class SuccessfulWorker:
    def __init__(self) -> None:
        self.request: ParserWorkRequest | None = None

    async def run(self, request: ParserWorkRequest) -> ParserWorkResult:
        self.request = request
        run: Final = ParserRun(
            parser_run_id=request.parser_run_id,
            channel_id=request.channel_id,
            parser_id="fixture-parser",
            parser_version="1.0.0",
            parsed_at=request.parsed_at,
            status=ParserRunStatus.PARTIAL,
            result=ParsedChannelData(warnings=("需要人工补充",)),
        )
        export: Final = ParserExportState(
            status=ParserExportStatus.SUCCEEDED,
            attempt_count=1,
            last_attempt_at=_NOW,
            exported_at=_NOW,
        )
        return ParserWorkSuccess(
            status="exported",
            run=run,
            persistence_status="created",
            export=export,
        )


def _actor() -> ActorContext:
    return ActorContext(
        user_id="admin-user",
        role="proxy_admin",
        request_id="request-123",
        action=ActorAction.PARSER_START,
        envelope_id=UUID("50000000-0000-0000-0000-000000000005"),
    )


def _request() -> ParserTaskStartRequest:
    return ParserTaskStartRequest(
        provider_id="openai_compatible",
        api_base=_API_BASE,
        api_key=SecretStr(_SECRET),
        openai_compatible=True,
    )


class FixedIdFactory:
    def __init__(self) -> None:
        self._values: tuple[UUID, ...] = (_TASK_ID, _RUN_ID)

    def __call__(self) -> UUID:
        value, *remaining = self._values
        self._values = tuple(remaining)
        return value


async def _wait_until_finished(service: ParserTaskService, attempts: int = 20) -> ParserTaskView:
    result: Final = await service.view(_CHANNEL_ID, _TASK_ID)
    assert isinstance(result, ParserTaskView)
    if result.task.status != ParserTaskStatus.RUNNING:
        return result
    if attempts == 0:
        raise AssertionError("parser task did not finish")
    await asyncio.sleep(0)
    return await _wait_until_finished(service, attempts=attempts - 1)


async def test_one_time_key_stays_out_of_persistent_task_and_public_view() -> None:
    repository: Final = FakeTaskRepository()
    provider: Final = GatedProvider()
    worker: Final = SuccessfulWorker()
    audit: Final = FakeAuditRepository()
    operations: Final = FakeOperationalRepository()
    service: Final = ParserTaskService(
        providers=provider,
        worker=worker,
        repository=repository,
        audit=audit,
        operations=operations,
        instance_id=_INSTANCE_ID,
        clock=lambda: _NOW,
        id_factory=FixedIdFactory(),
        heartbeat_interval_seconds=0.001,
    )

    accepted: Final = await service.start(_CHANNEL_ID, _request(), _actor())
    assert isinstance(accepted, ParserTaskAccepted)
    await provider.started.wait()
    running: Final = await service.view(_CHANNEL_ID, accepted.task_id)
    assert isinstance(running, ParserTaskView)
    persisted: Final = running.model_dump_json()
    assert _SECRET not in persisted
    assert _API_BASE not in persisted
    assert "api_key" not in persisted
    assert provider.request is not None
    assert provider.request.api_key.get_secret_value() == _SECRET
    assert len(audit.records) == 1
    assert audit.records[0].event.event_type == ManagementEventType.PARSER_TASK_START
    assert audit.records[0].audit.outcome.value == "accepted"
    assert _SECRET not in audit.records[0].model_dump_json()

    provider.release.set()
    finished: Final = await _wait_until_finished(service)
    assert finished.task.status == ParserTaskStatus.COMPLETED
    assert worker.request is not None
    assert worker.request.parser_run_id == _RUN_ID
    assert tuple(record.event.event_type for record in operations.records) == (
        OperationalEventType.PARSER_TASK_COMPLETED,
    )
    assert _SECRET not in operations.records[0].model_dump_json()
    assert _API_BASE not in operations.records[0].model_dump_json()
    await service.close()


async def test_initialize_marks_stale_foreign_task_interrupted_without_running_it() -> None:
    repository: Final = FakeTaskRepository()
    stale: Final = ParserTaskRecord(
        task_id=_TASK_ID,
        channel_id=_CHANNEL_ID,
        parser_run_id=_RUN_ID,
        provider_id="openai_compatible",
        openai_compatible=True,
        status=ParserTaskStatus.RUNNING,
        owner_instance_id=UUID("60000000-0000-0000-0000-000000000006"),
        actor_id="admin-user",
        actor_role="proxy_admin",
        request_id="request-old",
        created_at=_NOW - timedelta(minutes=2),
        heartbeat_at=_NOW - timedelta(minutes=2),
    )
    repository.records[_TASK_ID] = stale
    operations: Final = FakeOperationalRepository()
    service: Final = ParserTaskService(
        providers=GatedProvider(),
        worker=SuccessfulWorker(),
        repository=repository,
        audit=FakeAuditRepository(),
        operations=operations,
        instance_id=_INSTANCE_ID,
        clock=lambda: _NOW,
        stale_after_seconds=30,
    )

    await service.initialize()

    assert repository.swept_before == _NOW - timedelta(seconds=30)
    assert repository.records[_TASK_ID].status == ParserTaskStatus.INTERRUPTED_REQUIRES_KEY
    assert operations.records[0].event.event_type == OperationalEventType.PARSER_TASK_INTERRUPTED
    assert operations.records[0].event.safe_details.interruption_source == ParserTaskInterruptionSource.STALE_HEARTBEAT


async def test_audit_failure_prevents_provider_request_and_marks_created_task_failed() -> None:
    repository: Final = FakeTaskRepository()
    provider: Final = GatedProvider()
    operations: Final = FakeOperationalRepository()
    service: Final = ParserTaskService(
        providers=provider,
        worker=SuccessfulWorker(),
        repository=repository,
        audit=FailingAuditRepository(),
        operations=operations,
        instance_id=_INSTANCE_ID,
        clock=lambda: _NOW,
        id_factory=FixedIdFactory(),
    )

    result: Final = await service.start(_CHANNEL_ID, _request(), _actor())

    assert isinstance(result, ParserTaskOperationFailure)
    assert result.code == ParserTaskOperationFailureCode.AUDIT_UNAVAILABLE
    assert not provider.started.is_set()
    assert repository.records[_TASK_ID].status == ParserTaskStatus.FAILED
    assert operations.records[0].event.event_type == OperationalEventType.PARSER_TASK_FAILED
    assert operations.records[0].event.reason_code == ParserTaskFailureCode.INTERNAL


async def test_bounded_shutdown_marks_unfinished_local_task_as_requiring_key() -> None:
    repository: Final = FakeTaskRepository()
    provider: Final = GatedProvider()
    worker: Final = SuccessfulWorker()
    operations: Final = FakeOperationalRepository()
    service: Final = ParserTaskService(
        providers=provider,
        worker=worker,
        repository=repository,
        audit=FakeAuditRepository(),
        operations=operations,
        instance_id=_INSTANCE_ID,
        clock=lambda: _NOW,
        id_factory=FixedIdFactory(),
    )
    accepted: Final = await service.start(_CHANNEL_ID, _request(), _actor())
    assert isinstance(accepted, ParserTaskAccepted)
    await provider.started.wait()

    await service.close(timeout_seconds=0)

    assert repository.records[_TASK_ID].status == ParserTaskStatus.INTERRUPTED_REQUIRES_KEY
    assert repository.records[_TASK_ID].failure_code is None
    assert worker.request is None
    assert operations.records[0].event.event_type == OperationalEventType.PARSER_TASK_INTERRUPTED
    assert (
        operations.records[0].event.safe_details.interruption_source
        == ParserTaskInterruptionSource.GRACEFUL_SHUTDOWN
    )
