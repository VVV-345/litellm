"""声明解析任务持久化协议和不会抛出数据库异常的类型化结果。"""

from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime

from account_pool.models import FrozenModel
from account_pool.parsing.tasks.models import ParserTaskFailureCode, ParserTaskRecord, ParserTaskStatus


class ParserTaskPersistenceFailureCode(StrEnum):
    CHANNEL_NOT_FOUND = "channel_not_found"
    TASK_NOT_FOUND = "task_not_found"
    CONTENT_CONFLICT = "content_conflict"
    OWNERSHIP_CONFLICT = "ownership_conflict"
    INVALID_STORED_DATA = "invalid_stored_data"
    DATABASE_UNAVAILABLE = "database_unavailable"


class ParserTaskPersistenceFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: ParserTaskPersistenceFailureCode
    retryable: bool


class ParserTaskWriteSuccess(FrozenModel):
    status: Literal["created", "updated"]
    record: ParserTaskRecord


class ParserTaskLoadSuccess(FrozenModel):
    status: Literal["loaded"] = "loaded"
    record: ParserTaskRecord


class ParserTaskSweepSuccess(FrozenModel):
    status: Literal["swept"] = "swept"
    interrupted_tasks: tuple[ParserTaskRecord, ...] = ()

    @property
    def interrupted_task_ids(self) -> tuple[UUID, ...]:
        return tuple(task.task_id for task in self.interrupted_tasks)


ParserTaskWriteResult = ParserTaskWriteSuccess | ParserTaskPersistenceFailure
ParserTaskLoadResult = ParserTaskLoadSuccess | ParserTaskPersistenceFailure
ParserTaskSweepResult = ParserTaskSweepSuccess | ParserTaskPersistenceFailure


class ParserTaskRepository(Protocol):
    async def create(self, record: ParserTaskRecord) -> ParserTaskWriteResult: ...

    async def load(self, channel_id: UUID, task_id: UUID) -> ParserTaskLoadResult: ...

    async def heartbeat(self, task_id: UUID, owner_instance_id: UUID, at: AwareDatetime) -> ParserTaskWriteResult: ...

    async def finish(
        self,
        task_id: UUID,
        owner_instance_id: UUID,
        status: ParserTaskStatus,
        failure_code: ParserTaskFailureCode | None,
        at: AwareDatetime,
    ) -> ParserTaskWriteResult: ...

    async def sweep_stale(self, stale_before: AwareDatetime, at: AwareDatetime) -> ParserTaskSweepResult: ...
