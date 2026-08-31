"""定义人工覆盖设置、撤销、快照刷新和类型化失败的应用服务契约。"""

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue

from account_pool.models import FrozenModel
from account_pool.parsing.models import ParsedChannelData
from account_pool.parsing.overrides.composer import OverrideApplyFailure, OverrideApplyFailureCode
from account_pool.parsing.overrides.models import OverrideAction, OverrideTarget


class OverrideSetRequest(FrozenModel):
    override_id: UUID
    target: OverrideTarget
    value: JsonValue | None
    expected_override_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=1000)


class OverrideRevokeRequest(FrozenModel):
    override_id: UUID
    expected_override_id: UUID
    reason: str = Field(min_length=1, max_length=1000)


class OverrideMutationFailureCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    CHANNEL_NOT_FOUND = "channel_not_found"
    RUN_NOT_FOUND = "run_not_found"
    OVERRIDE_NOT_FOUND = "override_not_found"
    PREDECESSOR_CONFLICT = "predecessor_conflict"
    CONTENT_CONFLICT = "content_conflict"
    INVALID_VALUE = "invalid_value"
    INVALID_DATA = "invalid_data"
    DATABASE_UNAVAILABLE = "database_unavailable"
    RUNTIME_PROJECTION_FAILED = "runtime_projection_failed"
    AUDIT_UNAVAILABLE = "audit_unavailable"


class OverrideMutationFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: OverrideMutationFailureCode
    retryable: bool
    apply_failure_code: OverrideApplyFailureCode | None = None


class OverrideEventResult(FrozenModel):
    override_id: UUID
    field_path: str = Field(min_length=1)
    action: OverrideAction
    source_parser_run_id: UUID
    actor_id: str = Field(min_length=1)
    actor_role: str | None = None
    request_id: str | None = None
    occurred_at: AwareDatetime


class OverrideMutationSuccess(FrozenModel):
    status: Literal["created", "unchanged"]
    event: OverrideEventResult
    effective_result: ParsedChannelData
    applied_override_ids: tuple[UUID, ...] = ()
    override_failures: tuple[OverrideApplyFailure, ...] = ()


OverrideMutationResult = OverrideMutationSuccess | OverrideMutationFailure
