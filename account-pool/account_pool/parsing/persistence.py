"""定义解析运行持久化结果、快照导出状态和类型化仓储失败。"""

from __future__ import annotations

from enum import StrEnum
from typing import Final, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from account_pool.models import FrozenModel
from account_pool.parsing.models import ParserRun
from account_pool.parsing.snapshots import SnapshotExportFailureCode


class ParserExportStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"


class ParserPersistenceFailureCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    CHANNEL_NOT_FOUND = "channel_not_found"
    CONTENT_CONFLICT = "content_conflict"
    INVALID_RESULT = "invalid_result"
    RUN_NOT_FOUND = "run_not_found"
    INVALID_STORED_DATA = "invalid_stored_data"
    DATABASE_UNAVAILABLE = "database_unavailable"


class ParserExportState(FrozenModel):
    status: ParserExportStatus = ParserExportStatus.PENDING
    attempt_count: int = Field(default=0, ge=0)
    last_attempt_at: AwareDatetime | None = None
    exported_at: AwareDatetime | None = None
    failure_code: SnapshotExportFailureCode | None = None
    failure_retryable: bool | None = None

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.attempt_count == 0 and self.last_attempt_at is not None:
            raise ValueError("an unattempted export cannot have last_attempt_at")
        if self.attempt_count > 0 and self.last_attempt_at is None:
            raise ValueError("an attempted export requires last_attempt_at")
        if self.status == ParserExportStatus.PENDING:
            if (
                self.attempt_count != 0
                or self.exported_at is not None
                or self.failure_code is not None
                or self.failure_retryable is not None
            ):
                raise ValueError("a pending export cannot contain an outcome")
            return self
        if self.status == ParserExportStatus.SUCCEEDED:
            if self.exported_at is None:
                raise ValueError("a successful export requires exported_at")
            if self.failure_code is not None or self.failure_retryable is not None:
                raise ValueError("a successful export cannot contain failure details")
            return self
        if self.exported_at is not None or self.failure_code is None or self.failure_retryable is None:
            raise ValueError("a failed export requires failure details and cannot have exported_at")
        expected_retryable: Final = self.status == ParserExportStatus.RETRYABLE_FAILURE
        if self.failure_retryable != expected_retryable:
            raise ValueError("failure retryability must match export status")
        return self


class PersistedParserRun(FrozenModel):
    run: ParserRun
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    export: ParserExportState = ParserExportState()


class ParserRunWriteSuccess(FrozenModel):
    status: Literal["created", "unchanged"]
    record: PersistedParserRun


class ParserPersistenceFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: ParserPersistenceFailureCode
    retryable: bool


ParserRunWriteResult = ParserRunWriteSuccess | ParserPersistenceFailure


class ParserRunsLoadSuccess(FrozenModel):
    status: Literal["loaded"] = "loaded"
    records: tuple[PersistedParserRun, ...] = ()


ParserRunsLoadResult = ParserRunsLoadSuccess | ParserPersistenceFailure


class ParserExportAttempt(FrozenModel):
    attempted_at: AwareDatetime
    failure_code: SnapshotExportFailureCode | None = None
    failure_retryable: bool | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if (self.failure_code is None) != (self.failure_retryable is None):
            raise ValueError("export failure code and retryability must be set together")
        return self

    def next_state(self, previous_attempt_count: int) -> ParserExportState:
        attempt_count: Final = previous_attempt_count + 1
        if self.failure_code is None:
            return ParserExportState(
                status=ParserExportStatus.SUCCEEDED,
                attempt_count=attempt_count,
                last_attempt_at=self.attempted_at,
                exported_at=self.attempted_at,
            )
        retryable: Final = self.failure_retryable is True
        return ParserExportState(
            status=(
                ParserExportStatus.RETRYABLE_FAILURE if retryable else ParserExportStatus.PERMANENT_FAILURE
            ),
            attempt_count=attempt_count,
            last_attempt_at=self.attempted_at,
            failure_code=self.failure_code,
            failure_retryable=retryable,
        )


class ParserExportUpdateSuccess(FrozenModel):
    status: Literal["updated"] = "updated"
    parser_run_id: UUID
    export: ParserExportState


ParserExportUpdateResult = ParserExportUpdateSuccess | ParserPersistenceFailure
