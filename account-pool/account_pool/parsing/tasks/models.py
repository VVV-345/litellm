"""定义不含凭证的解析任务请求、持久化状态和公开响应。"""

from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, SecretStr, model_validator

from account_pool.models import FrozenModel


class ParserTaskStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED_REQUIRES_KEY = "interrupted_requires_key"


class ParserTaskFailureCode(StrEnum):
    WORKER_PERSISTENCE = "worker_persistence"
    WORKER_OVERRIDES = "worker_overrides"
    WORKER_EXPORT_STATE = "worker_export_state"
    INTERNAL = "internal"


class ParserTaskOperationFailureCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    CHANNEL_NOT_FOUND = "channel_not_found"
    TASK_NOT_FOUND = "task_not_found"
    CONFLICT = "conflict"
    DATABASE_UNAVAILABLE = "database_unavailable"
    INVALID_DATA = "invalid_data"


class ParserTaskStartRequest(FrozenModel):
    provider_id: str = Field(min_length=1, max_length=100)
    api_base: str = Field(min_length=1, max_length=2048)
    api_key: SecretStr
    group: str | None = Field(default=None, max_length=255)
    explicit_parser_id: str | None = Field(default=None, min_length=1, max_length=100)
    openai_compatible: bool = False


class ParserTaskRecord(FrozenModel):
    task_id: UUID
    channel_id: UUID
    parser_run_id: UUID
    provider_id: str = Field(min_length=1, max_length=100)
    explicit_parser_id: str | None = Field(default=None, min_length=1, max_length=100)
    openai_compatible: bool
    status: ParserTaskStatus
    owner_instance_id: UUID
    actor_id: str = Field(min_length=1, max_length=255)
    actor_role: Literal["proxy_admin"]
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    created_at: AwareDatetime
    heartbeat_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    failure_code: ParserTaskFailureCode | None = None

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.status == ParserTaskStatus.RUNNING:
            if self.completed_at is not None or self.failure_code is not None:
                raise ValueError("a running parser task cannot contain a completion outcome")
            return self
        if self.completed_at is None:
            raise ValueError("a finished parser task requires completed_at")
        if self.status == ParserTaskStatus.FAILED and self.failure_code is None:
            raise ValueError("a failed parser task requires failure_code")
        if self.status != ParserTaskStatus.FAILED and self.failure_code is not None:
            raise ValueError("only a failed parser task can contain failure_code")
        return self


class ParserTaskAccepted(FrozenModel):
    status: Literal["accepted"] = "accepted"
    task_id: UUID
    channel_id: UUID
    parser_run_id: UUID


class ParserTaskView(FrozenModel):
    status: Literal["loaded"] = "loaded"
    task: ParserTaskRecord


class ParserTaskOperationFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: ParserTaskOperationFailureCode
    retryable: bool


ParserTaskStartResult = ParserTaskAccepted | ParserTaskOperationFailure
ParserTaskViewResult = ParserTaskView | ParserTaskOperationFailure
