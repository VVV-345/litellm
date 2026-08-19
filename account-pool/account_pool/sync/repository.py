"""声明同步操作持久化协议、类型化结果和幂等内容比较。"""

from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime

from account_pool.models import FrozenModel
from account_pool.sync.models import SafeSyncFailure, SyncOperation


class SyncOperationPersistenceFailureCode(StrEnum):
    OPERATION_NOT_FOUND = "operation_not_found"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    STATE_CONFLICT = "state_conflict"
    INVALID_STORED_DATA = "invalid_stored_data"
    DATABASE_UNAVAILABLE = "database_unavailable"


class SyncOperationPersistenceFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: SyncOperationPersistenceFailureCode
    retryable: bool


class SyncOperationWriteSuccess(FrozenModel):
    status: Literal["created", "existing", "updated"]
    operation: SyncOperation


class SyncOperationLoadSuccess(FrozenModel):
    status: Literal["loaded"] = "loaded"
    operation: SyncOperation


class SyncOperationListSuccess(FrozenModel):
    status: Literal["loaded"] = "loaded"
    operations: tuple[SyncOperation, ...]


SyncOperationWriteResult = SyncOperationWriteSuccess | SyncOperationPersistenceFailure
SyncOperationLoadResult = SyncOperationLoadSuccess | SyncOperationPersistenceFailure
SyncOperationListResult = SyncOperationListSuccess | SyncOperationPersistenceFailure


def same_operation_request(left: SyncOperation, right: SyncOperation) -> bool:
    return (
        left.idempotency_key == right.idempotency_key
        and left.channel_id == right.channel_id
        and left.action == right.action
        and left.delete_mode == right.delete_mode
        and left.desired == right.desired
    )


class SyncOperationRepository(Protocol):
    async def create(self, operation: SyncOperation) -> SyncOperationWriteResult: ...

    async def load(self, operation_id: UUID) -> SyncOperationLoadResult: ...

    async def load_by_idempotency_key(self, idempotency_key: str) -> SyncOperationLoadResult: ...

    async def list_pending_and_failed(self, limit: int = 100) -> SyncOperationListResult: ...

    async def record_attempt(self, operation_id: UUID, at: AwareDatetime) -> SyncOperationWriteResult: ...

    async def mark_applied(self, operation_id: UUID, at: AwareDatetime) -> SyncOperationWriteResult: ...

    async def mark_failed(
        self,
        operation_id: UUID,
        failure: SafeSyncFailure,
        requires_key: bool,
        at: AwareDatetime,
    ) -> SyncOperationWriteResult: ...
