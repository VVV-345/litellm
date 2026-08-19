"""声明额度代次、usage 事件和运行快照的持久化仓储协议。"""

from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime

from account_pool.models import FrozenModel
from account_pool.quota.persistence_models import (
    QuotaRecoveryState,
    QuotaRuntimeGeneration,
    QuotaUsageEvent,
    QuotaWindowRuntimeSnapshot,
)


class QuotaPersistenceFailureCode(StrEnum):
    GENERATION_NOT_FOUND = "generation_not_found"
    ACTIVE_GENERATION_NOT_FOUND = "active_generation_not_found"
    CONTENT_CONFLICT = "content_conflict"
    STATE_CONFLICT = "state_conflict"
    INVALID_STORED_DATA = "invalid_stored_data"
    DATABASE_UNAVAILABLE = "database_unavailable"


class QuotaPersistenceFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: QuotaPersistenceFailureCode
    retryable: bool


class QuotaGenerationWriteSuccess(FrozenModel):
    status: Literal["created", "unchanged", "updated"]
    generation: QuotaRuntimeGeneration


class QuotaUsageWriteSuccess(FrozenModel):
    status: Literal["persisted"] = "persisted"
    events: tuple[QuotaUsageEvent, ...]


class QuotaSnapshotWriteSuccess(FrozenModel):
    status: Literal["persisted"] = "persisted"
    snapshots: tuple[QuotaWindowRuntimeSnapshot, ...]


class QuotaRecoveryLoadSuccess(FrozenModel):
    status: Literal["loaded"] = "loaded"
    state: QuotaRecoveryState


QuotaGenerationWriteResult = QuotaGenerationWriteSuccess | QuotaPersistenceFailure
QuotaUsageWriteResult = QuotaUsageWriteSuccess | QuotaPersistenceFailure
QuotaSnapshotWriteResult = QuotaSnapshotWriteSuccess | QuotaPersistenceFailure
QuotaRecoveryLoadResult = QuotaRecoveryLoadSuccess | QuotaPersistenceFailure


class QuotaRuntimeRepository(Protocol):
    async def begin_generation(self, generation: QuotaRuntimeGeneration) -> QuotaGenerationWriteResult: ...

    async def activate_generation(self, generation_id: UUID, at: AwareDatetime) -> QuotaGenerationWriteResult: ...

    async def fail_generation(
        self,
        generation_id: UUID,
        failure_code: str,
        at: AwareDatetime,
    ) -> QuotaGenerationWriteResult: ...

    async def append_usage(self, events: tuple[QuotaUsageEvent, ...]) -> QuotaUsageWriteResult: ...

    async def save_snapshots(
        self,
        snapshots: tuple[QuotaWindowRuntimeSnapshot, ...],
    ) -> QuotaSnapshotWriteResult: ...

    async def load_active_recovery_state(self) -> QuotaRecoveryLoadResult: ...
