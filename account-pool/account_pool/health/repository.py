"""声明健康事件、最近活动时间和详情查询的 PostgreSQL 仓储契约。"""

from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from account_pool.health.models import HealthActivity, HealthEventRecord, HealthRequestActivity
from account_pool.models import FrozenModel


class HealthPersistenceFailureCode(StrEnum):
    EVENT_NOT_FOUND = "event_not_found"
    CONTENT_CONFLICT = "content_conflict"
    INVALID_STORED_DATA = "invalid_stored_data"
    DATABASE_UNAVAILABLE = "database_unavailable"


class HealthPersistenceFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: HealthPersistenceFailureCode
    retryable: bool


class HealthWriteSuccess(FrozenModel):
    status: Literal["created", "unchanged"]
    record: HealthEventRecord


class HealthActivityWriteSuccess(FrozenModel):
    status: Literal["updated"] = "updated"
    activity: HealthRequestActivity


class HealthLoadSuccess(FrozenModel):
    status: Literal["loaded"] = "loaded"
    record: HealthEventRecord


class HealthActivityLoadSuccess(FrozenModel):
    status: Literal["loaded"] = "loaded"
    activities: tuple[HealthActivity, ...]


class HealthEventListSuccess(FrozenModel):
    status: Literal["loaded"] = "loaded"
    records: tuple[HealthEventRecord, ...]


HealthWriteResult = HealthWriteSuccess | HealthPersistenceFailure
HealthActivityWriteResult = HealthActivityWriteSuccess | HealthPersistenceFailure
HealthLoadResult = HealthLoadSuccess | HealthPersistenceFailure
HealthActivityLoadResult = HealthActivityLoadSuccess | HealthPersistenceFailure
HealthEventListResult = HealthEventListSuccess | HealthPersistenceFailure


class HealthEventRepository(Protocol):
    async def append(self, record: HealthEventRecord) -> HealthWriteResult: ...

    async def record_request(self, activity: HealthRequestActivity) -> HealthActivityWriteResult: ...

    async def load(self, event_id: UUID) -> HealthLoadResult: ...

    async def load_activity(self) -> HealthActivityLoadResult: ...

    async def list_recent(self, channel_id: UUID, limit: int = 50) -> HealthEventListResult: ...
