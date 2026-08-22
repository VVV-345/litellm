"""声明系统运行事件的持久化协议和类型化失败结果。"""

from enum import StrEnum
from typing import Literal, Protocol

from account_pool.models import FrozenModel
from account_pool.operational.models import OperationalEventRecord


class OperationalPersistenceFailureCode(StrEnum):
    CONTENT_CONFLICT = "content_conflict"
    INVALID_STORED_DATA = "invalid_stored_data"
    DATABASE_UNAVAILABLE = "database_unavailable"


class OperationalPersistenceFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: OperationalPersistenceFailureCode
    retryable: bool


class OperationalWriteSuccess(FrozenModel):
    status: Literal["created", "unchanged"]
    record: OperationalEventRecord


OperationalWriteResult = OperationalWriteSuccess | OperationalPersistenceFailure


class OperationalEventRepository(Protocol):
    async def append(self, record: OperationalEventRecord) -> OperationalWriteResult: ...
