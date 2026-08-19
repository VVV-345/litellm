"""声明管理审计事件的原子追加仓储协议和类型化结果。"""

from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from account_pool.audit.models import ManagementAuditRecord
from account_pool.models import FrozenModel


class AuditPersistenceFailureCode(StrEnum):
    EVENT_NOT_FOUND = "event_not_found"
    CONTENT_CONFLICT = "content_conflict"
    INVALID_STORED_DATA = "invalid_stored_data"
    DATABASE_UNAVAILABLE = "database_unavailable"


class AuditPersistenceFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: AuditPersistenceFailureCode
    retryable: bool


class AuditWriteSuccess(FrozenModel):
    status: Literal["created", "unchanged"]
    record: ManagementAuditRecord


class AuditLoadSuccess(FrozenModel):
    status: Literal["loaded"] = "loaded"
    record: ManagementAuditRecord


AuditWriteResult = AuditWriteSuccess | AuditPersistenceFailure
AuditLoadResult = AuditLoadSuccess | AuditPersistenceFailure


class ManagementAuditRepository(Protocol):
    async def append(self, record: ManagementAuditRecord) -> AuditWriteResult: ...

    async def load(self, event_id: UUID) -> AuditLoadResult: ...
