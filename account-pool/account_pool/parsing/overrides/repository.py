"""声明人工覆盖事件仓储协议和类型化读写结果。"""

from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from account_pool.models import FrozenModel
from account_pool.parsing.overrides.models import FieldOverrideEvent


class OverridePersistenceFailureCode(StrEnum):
    CHANNEL_NOT_FOUND = "channel_not_found"
    SOURCE_RUN_NOT_FOUND = "source_run_not_found"
    PREDECESSOR_CONFLICT = "predecessor_conflict"
    CONTENT_CONFLICT = "content_conflict"
    INVALID_STORED_DATA = "invalid_stored_data"
    DATABASE_UNAVAILABLE = "database_unavailable"


class OverridePersistenceFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: OverridePersistenceFailureCode
    retryable: bool


class OverrideWriteSuccess(FrozenModel):
    status: Literal["created", "unchanged"]
    event: FieldOverrideEvent


class OverrideBatchWriteSuccess(FrozenModel):
    status: Literal["created", "unchanged"]
    events: tuple[FieldOverrideEvent, ...]


OverrideWriteResult = OverrideWriteSuccess | OverridePersistenceFailure
OverrideBatchWriteResult = OverrideBatchWriteSuccess | OverridePersistenceFailure


class OverrideEventsLoadSuccess(FrozenModel):
    status: Literal["loaded"] = "loaded"
    events: tuple[FieldOverrideEvent, ...] = ()


OverrideEventsLoadResult = OverrideEventsLoadSuccess | OverridePersistenceFailure


class OverrideEventRepository(Protocol):
    async def append(self, event: FieldOverrideEvent) -> OverrideWriteResult: ...

    async def load_for_channel(self, channel_id: UUID) -> OverrideEventsLoadResult: ...


class OverrideEventBatchRepository(Protocol):
    async def append_batch(self, events: tuple[FieldOverrideEvent, ...]) -> OverrideBatchWriteResult: ...
