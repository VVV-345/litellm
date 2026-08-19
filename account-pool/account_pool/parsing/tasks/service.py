"""在当前实例内执行一次性 Key 解析任务，并用持久心跳标记不可接管的中断。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol
from uuid import UUID, uuid4

from pydantic import AwareDatetime

from account_pool.auth.actor import ActorAction, ActorContext
from account_pool.domain.provider_source import ProviderValidationRequest, ProviderValidationResult
from account_pool.parsing.registry import ParserSelectionRequest
from account_pool.parsing.tasks.models import (
    ParserTaskAccepted,
    ParserTaskFailureCode,
    ParserTaskOperationFailure,
    ParserTaskOperationFailureCode,
    ParserTaskRecord,
    ParserTaskStartRequest,
    ParserTaskStartResult,
    ParserTaskStatus,
    ParserTaskView,
    ParserTaskViewResult,
)
from account_pool.parsing.tasks.repository import (
    ParserTaskPersistenceFailure,
    ParserTaskPersistenceFailureCode,
    ParserTaskRepository,
    ParserTaskWriteSuccess,
)
from account_pool.parsing.worker import ParserWorkFailure, ParserWorkRequest, ParserWorkResult

Clock = Callable[[], AwareDatetime]
IdFactory = Callable[[], UUID]


class ParserTaskManager(Protocol):
    async def initialize(self) -> None: ...

    async def start(
        self,
        channel_id: UUID,
        request: ParserTaskStartRequest,
        actor: ActorContext,
    ) -> ParserTaskStartResult: ...

    async def view(self, channel_id: UUID, task_id: UUID) -> ParserTaskViewResult: ...

    async def close(self, timeout_seconds: float = 10) -> None: ...


class ProviderValidator(Protocol):
    async def validate(self, request: ProviderValidationRequest) -> ProviderValidationResult: ...


class ParserWorkerRunner(Protocol):
    async def run(self, request: ParserWorkRequest) -> ParserWorkResult: ...


def utc_now() -> AwareDatetime:
    return datetime.now(UTC)


class ParserTaskService:
    def __init__(
        self,
        providers: ProviderValidator,
        worker: ParserWorkerRunner,
        repository: ParserTaskRepository,
        *,
        instance_id: UUID | None = None,
        clock: Clock = utc_now,
        id_factory: IdFactory = uuid4,
        heartbeat_interval_seconds: float = 5,
        stale_after_seconds: int = 30,
    ) -> None:
        self._providers: Final = providers
        self._worker: Final = worker
        self._repository: Final = repository
        self._instance_id: Final = instance_id or uuid4()
        self._clock: Final = clock
        self._id_factory: Final = id_factory
        self._heartbeat_interval_seconds: Final = heartbeat_interval_seconds
        self._stale_after_seconds: Final = stale_after_seconds
        self._tasks: Final[dict[UUID, asyncio.Task[None]]] = {}

    async def initialize(self) -> None:
        now: Final = self._clock()
        await self._repository.sweep_stale(
            stale_before=now - timedelta(seconds=self._stale_after_seconds),
            at=now,
        )

    async def start(
        self,
        channel_id: UUID,
        request: ParserTaskStartRequest,
        actor: ActorContext,
    ) -> ParserTaskStartResult:
        if actor.action != ActorAction.PARSER_START:
            return _failure(ParserTaskOperationFailureCode.INVALID_REQUEST, retryable=False)
        task_id: Final = self._id_factory()
        parser_run_id: Final = self._id_factory()
        now: Final = self._clock()
        record: Final = ParserTaskRecord(
            task_id=task_id,
            channel_id=channel_id,
            parser_run_id=parser_run_id,
            provider_id=request.provider_id,
            explicit_parser_id=request.explicit_parser_id,
            openai_compatible=request.openai_compatible,
            status=ParserTaskStatus.RUNNING,
            owner_instance_id=self._instance_id,
            actor_id=actor.user_id,
            actor_role=actor.role,
            request_id=actor.request_id,
            created_at=now,
            heartbeat_at=now,
        )
        created: Final = await self._repository.create(record)
        if isinstance(created, ParserTaskPersistenceFailure):
            return _from_persistence_failure(created)
        local_task: Final = asyncio.create_task(self._execute(record, request))
        self._tasks[task_id] = local_task
        local_task.add_done_callback(lambda _: self._tasks.pop(task_id, None))
        return ParserTaskAccepted(task_id=task_id, channel_id=channel_id, parser_run_id=parser_run_id)

    async def view(self, channel_id: UUID, task_id: UUID) -> ParserTaskViewResult:
        loaded: Final = await self._repository.load(channel_id=channel_id, task_id=task_id)
        if isinstance(loaded, ParserTaskPersistenceFailure):
            return _from_persistence_failure(loaded)
        return ParserTaskView(task=loaded.record)

    async def close(self, timeout_seconds: float = 10) -> None:
        running: Final = tuple(self._tasks.items())
        if not running:
            return
        _, pending = await asyncio.wait(
            tuple(task for _, task in running),
            timeout=timeout_seconds,
        )
        if not pending:
            return
        pending_ids: Final = tuple(task_id for task_id, task in running if task in pending)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        at: Final = self._clock()
        for task_id in pending_ids:
            await self._repository.finish(
                task_id=task_id,
                owner_instance_id=self._instance_id,
                status=ParserTaskStatus.INTERRUPTED_REQUIRES_KEY,
                failure_code=None,
                at=at,
            )

    async def _execute(self, record: ParserTaskRecord, request: ParserTaskStartRequest) -> None:
        heartbeat: Final = asyncio.create_task(self._heartbeat(record.task_id))
        try:
            validation: Final = await self._providers.validate(
                ProviderValidationRequest(
                    provider_id=request.provider_id,
                    api_base=request.api_base,
                    api_key=request.api_key,
                    group=request.group,
                )
            )
            outcome: Final = await self._worker.run(
                ParserWorkRequest(
                    channel_id=record.channel_id,
                    parser_run_id=record.parser_run_id,
                    parsed_at=record.created_at,
                    selection=ParserSelectionRequest(
                        provider_id=request.provider_id,
                        api_base=request.api_base,
                        explicit_parser_id=request.explicit_parser_id,
                        openai_compatible=request.openai_compatible,
                    ),
                    validation=validation,
                )
            )
            failure_code: Final = _worker_failure_code(outcome) if isinstance(outcome, ParserWorkFailure) else None
            await self._repository.finish(
                task_id=record.task_id,
                owner_instance_id=self._instance_id,
                status=ParserTaskStatus.FAILED if failure_code is not None else ParserTaskStatus.COMPLETED,
                failure_code=failure_code,
                at=self._clock(),
            )
        except asyncio.CancelledError:
            return
        except Exception:
            await self._repository.finish(
                task_id=record.task_id,
                owner_instance_id=self._instance_id,
                status=ParserTaskStatus.FAILED,
                failure_code=ParserTaskFailureCode.INTERNAL,
                at=self._clock(),
            )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

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
        return isinstance(updated, ParserTaskWriteSuccess)


def _worker_failure_code(failure: ParserWorkFailure) -> ParserTaskFailureCode:
    if failure.stage == "persistence":
        return ParserTaskFailureCode.WORKER_PERSISTENCE
    if failure.stage == "overrides":
        return ParserTaskFailureCode.WORKER_OVERRIDES
    return ParserTaskFailureCode.WORKER_EXPORT_STATE


def _from_persistence_failure(failure: ParserTaskPersistenceFailure) -> ParserTaskOperationFailure:
    if failure.code == ParserTaskPersistenceFailureCode.CHANNEL_NOT_FOUND:
        return _failure(ParserTaskOperationFailureCode.CHANNEL_NOT_FOUND, retryable=False)
    if failure.code == ParserTaskPersistenceFailureCode.TASK_NOT_FOUND:
        return _failure(ParserTaskOperationFailureCode.TASK_NOT_FOUND, retryable=False)
    if failure.code in (
        ParserTaskPersistenceFailureCode.CONTENT_CONFLICT,
        ParserTaskPersistenceFailureCode.OWNERSHIP_CONFLICT,
    ):
        return _failure(ParserTaskOperationFailureCode.CONFLICT, retryable=False)
    if failure.code == ParserTaskPersistenceFailureCode.DATABASE_UNAVAILABLE:
        return _failure(ParserTaskOperationFailureCode.DATABASE_UNAVAILABLE, retryable=True)
    return _failure(ParserTaskOperationFailureCode.INVALID_DATA, retryable=False)


def _failure(code: ParserTaskOperationFailureCode, retryable: bool) -> ParserTaskOperationFailure:
    return ParserTaskOperationFailure(code=code, retryable=retryable)
