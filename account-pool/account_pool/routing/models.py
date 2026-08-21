"""定义不依赖数据库或运行后端的调度候选和排序结果。"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from account_pool.models import AccountId, ModelName, Strategy


class RoutingModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RoutingCandidate(RoutingModel):
    account_id: AccountId
    deployment_id: str = Field(min_length=1)
    billing_route_id: str | None = Field(default=None, min_length=1)
    available: bool
    priority: int
    weight: int = Field(ge=1, le=100)
    manual_order: int | None = Field(default=None, ge=0)
    inflight: int = Field(ge=0)
    max_concurrency: int = Field(ge=1)
    remaining_quota_ratio: float | None = Field(default=None, ge=0)
    latency_ewma_ms: float | None = Field(default=None, ge=0)
    effective_cost: Decimal | None = Field(default=None, ge=0)

    def stable_id(self) -> str:
        return f"{self.account_id}\x00{self.deployment_id}\x00{self.billing_route_id or ''}"


class RoutingOrder(RoutingModel):
    candidate: RoutingCandidate
    reason_codes: tuple[str, ...] = ()
    dynamic: bool = False


class RoutingCandidateOverride(RoutingModel):
    binding_id: UUID
    manual_order: int | None = Field(default=None, ge=0)
    weight: int | None = Field(default=None, ge=1, le=100)
    paused: bool = False


class RoutingPolicyState(RoutingModel):
    status: Literal["loaded"] = "loaded"
    model: ModelName
    strategy: Strategy
    version: int = Field(ge=0)
    overrides: tuple[RoutingCandidateOverride, ...] = ()


class RoutingPolicyMutation(RoutingModel):
    expected_version: int = Field(ge=0)
    strategy: Strategy


class RoutingCandidateMutation(RoutingModel):
    expected_version: int = Field(ge=0)
    manual_order: int | None = Field(default=None, ge=0)
    weight: int | None = Field(default=None, ge=1, le=100)
    paused: bool = False


class RoutingVersionMutation(RoutingModel):
    expected_version: int = Field(ge=0)


class RoutingFailureCode(StrEnum):
    INVALID_ACTOR = "invalid_actor"
    MODEL_NOT_FOUND = "model_not_found"
    BINDING_NOT_FOUND = "binding_not_found"
    VERSION_CONFLICT = "version_conflict"
    CANDIDATE_CONFLICT = "candidate_conflict"
    DATABASE_UNAVAILABLE = "database_unavailable"
    RUNTIME_PROJECTION_FAILED = "runtime_projection_failed"
    AUDIT_UNAVAILABLE = "audit_unavailable"


class RoutingFailure(RoutingModel):
    status: Literal["failed"] = "failed"
    code: RoutingFailureCode
    retryable: bool
    current_version: int | None = Field(default=None, ge=0)


RoutingPolicyResult = RoutingPolicyState | RoutingFailure
