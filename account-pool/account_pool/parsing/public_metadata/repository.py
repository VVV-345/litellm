"""声明公开元数据任务队列的持久化协议和类型化结果。"""

from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime

from account_pool.models import FrozenModel
from account_pool.parsing.public_metadata.models import (
    PublicMetadataTaskFailureCode,
    PublicMetadataTaskRecord,
    PublicMetadataTaskStatus,
)


class PublicMetadataPersistenceFailureCode(StrEnum):
    DATABASE_UNAVAILABLE = "database_unavailable"
    INVALID_STORED_DATA = "invalid_stored_data"
    OWNERSHIP_CONFLICT = "ownership_conflict"


class PublicMetadataPersistenceFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: PublicMetadataPersistenceFailureCode
    retryable: bool


class PublicMetadataScheduleSuccess(FrozenModel):
    status: Literal["created", "unchanged"]
    record: PublicMetadataTaskRecord | None = None


class PublicMetadataClaimSuccess(FrozenModel):
    status: Literal["claimed", "empty"]
    record: PublicMetadataTaskRecord | None = None


class PublicMetadataWriteSuccess(FrozenModel):
    status: Literal["updated"] = "updated"
    record: PublicMetadataTaskRecord


class PublicMetadataRecoverySuccess(FrozenModel):
    status: Literal["recovered"] = "recovered"
    records: tuple[PublicMetadataTaskRecord, ...] = ()


PublicMetadataScheduleResult = PublicMetadataScheduleSuccess | PublicMetadataPersistenceFailure
PublicMetadataClaimResult = PublicMetadataClaimSuccess | PublicMetadataPersistenceFailure
PublicMetadataWriteResult = PublicMetadataWriteSuccess | PublicMetadataPersistenceFailure
PublicMetadataRecoveryResult = PublicMetadataRecoverySuccess | PublicMetadataPersistenceFailure


class PublicMetadataTaskRepository(Protocol):
    async def schedule(
        self, record: PublicMetadataTaskRecord, refresh_after: AwareDatetime
    ) -> PublicMetadataScheduleResult: ...

    async def recover_stale(
        self,
        stale_before: AwareDatetime,
        at: AwareDatetime,
    ) -> PublicMetadataRecoveryResult: ...

    async def claim_next(
        self,
        owner_instance_id: UUID,
        at: AwareDatetime,
    ) -> PublicMetadataClaimResult: ...

    async def heartbeat(
        self,
        task_id: UUID,
        owner_instance_id: UUID,
        at: AwareDatetime,
    ) -> PublicMetadataWriteResult: ...

    async def retry(
        self,
        task_id: UUID,
        owner_instance_id: UUID,
        parser_run_id: UUID,
        failure_code: PublicMetadataTaskFailureCode,
        next_attempt_at: AwareDatetime,
        at: AwareDatetime,
    ) -> PublicMetadataWriteResult: ...

    async def finish(
        self,
        task_id: UUID,
        owner_instance_id: UUID,
        status: PublicMetadataTaskStatus,
        failure_code: PublicMetadataTaskFailureCode | None,
        at: AwareDatetime,
    ) -> PublicMetadataWriteResult: ...
