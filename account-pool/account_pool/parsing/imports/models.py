"""定义受控快照导入的单渠道文档、失败分类和公开结果。"""

from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from account_pool.models import FrozenModel
from account_pool.parsing.models import ParsedChannelData
from account_pool.parsing.overrides.commands import OverrideEventResult
from account_pool.parsing.overrides.composer import OverrideApplyFailure
from account_pool.parsing.snapshots import ParserSnapshot


class SnapshotImportRequest(FrozenModel):
    import_id: UUID
    reason: str = Field(min_length=1, max_length=1000)
    document: dict[UUID, ParserSnapshot]

    @model_validator(mode="after")
    def validate_single_channel(self) -> Self:
        if len(self.document) != 1:
            raise ValueError("snapshot import requires exactly one channel document")
        return self


class SnapshotImportFailureCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    CHANNEL_NOT_FOUND = "channel_not_found"
    RUN_NOT_FOUND = "run_not_found"
    PREDECESSOR_CONFLICT = "predecessor_conflict"
    CONTENT_CONFLICT = "content_conflict"
    INVALID_DATA = "invalid_data"
    DATABASE_UNAVAILABLE = "database_unavailable"


class SnapshotImportFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: SnapshotImportFailureCode
    retryable: bool


class SnapshotImportSuccess(FrozenModel):
    status: Literal["created", "unchanged"]
    import_id: UUID
    channel_id: UUID
    source_parser_run_id: UUID
    events: tuple[OverrideEventResult, ...] = ()
    effective_result: ParsedChannelData
    applied_override_ids: tuple[UUID, ...] = ()
    override_failures: tuple[OverrideApplyFailure, ...] = ()


SnapshotImportResult = SnapshotImportSuccess | SnapshotImportFailure
