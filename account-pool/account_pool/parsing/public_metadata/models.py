"""定义无凭证公开元数据任务、重试状态和安全渠道输入。"""

from __future__ import annotations

from enum import StrEnum
from typing import Final, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from account_pool.models import FrozenModel


class PublicMetadataTaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"
    FAILED = "failed"


class PublicMetadataTaskFailureCode(StrEnum):
    SOURCE_TRANSPORT = "source_transport"
    SOURCE_INVALID_RESPONSE = "source_invalid_response"
    SOURCE_UNAVAILABLE = "source_unavailable"
    UNSAFE_SOURCE_RESULT = "unsafe_source_result"
    WORKER_PERSISTENCE = "worker_persistence"
    WORKER_OVERRIDES = "worker_overrides"
    WORKER_EXPORT_STATE = "worker_export_state"
    WORKER_LOST = "worker_lost"
    CHANNEL_UNAVAILABLE = "channel_unavailable"
    INTERNAL = "internal"


class PublicMetadataChannel(FrozenModel):
    channel_id: UUID
    provider_id: str = Field(min_length=1, max_length=100)
    api_base: str = Field(min_length=1, max_length=2048)
    group: str | None = Field(default=None, max_length=255)


class PublicMetadataTaskRecord(FrozenModel):
    task_id: UUID
    channel_id: UUID
    parser_run_id: UUID
    provider_id: str = Field(min_length=1, max_length=100)
    status: PublicMetadataTaskStatus
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1, le=10)
    owner_instance_id: UUID | None = None
    next_attempt_at: AwareDatetime
    created_at: AwareDatetime
    updated_at: AwareDatetime
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    failure_code: PublicMetadataTaskFailureCode | None = None

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        running: Final = self.status == PublicMetadataTaskStatus.RUNNING
        terminal: Final = self.status in (PublicMetadataTaskStatus.COMPLETED, PublicMetadataTaskStatus.FAILED)
        failed: Final = self.status in (PublicMetadataTaskStatus.RETRY_WAIT, PublicMetadataTaskStatus.FAILED)
        if running != (self.owner_instance_id is not None):
            raise ValueError("only running public metadata tasks have an owner")
        if running and (self.started_at is None or self.attempt_count == 0):
            raise ValueError("running public metadata tasks require a started attempt")
        if (self.status == PublicMetadataTaskStatus.QUEUED) != (self.started_at is None):
            raise ValueError("only queued public metadata tasks can be unstarted")
        if terminal != (self.completed_at is not None):
            raise ValueError("only terminal public metadata tasks have a completion time")
        if failed != (self.failure_code is not None):
            raise ValueError("public metadata task failure state does not match its failure code")
        if self.status == PublicMetadataTaskStatus.QUEUED and self.attempt_count != 0:
            raise ValueError("queued public metadata tasks cannot have attempts")
        if self.attempt_count > self.max_attempts:
            raise ValueError("public metadata task attempts exceed the configured maximum")
        return self
