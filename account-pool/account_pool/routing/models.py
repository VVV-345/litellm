"""定义不依赖数据库或运行后端的调度候选和排序结果。"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    remaining_quota: Decimal | None = Field(default=None, ge=0)
    remaining_quota_unit: str | None = Field(default=None, min_length=1)
    quota_unavailable_reason: str | None = Field(default=None, min_length=1)
    latency_ewma_ms: float | None = Field(default=None, ge=0)
    effective_cost: Decimal | None = Field(default=None, ge=0)
    cost_currency: str | None = Field(default=None, min_length=1)
    cost_unit: str | None = Field(default=None, min_length=1)
    cost_partial: bool = False
    cost_included: bool = False

    @model_validator(mode="after")
    def validate_cost_basis(self) -> RoutingCandidate:
        if (self.remaining_quota is None) != (self.remaining_quota_unit is None):
            raise ValueError("remaining quota requires a unit")
        has_basis = self.cost_currency is not None and self.cost_unit is not None
        if self.cost_included:
            if self.effective_cost != 0 or has_basis:
                raise ValueError("included cost must be zero without a currency basis")
            return self
        if (self.effective_cost is not None) != has_basis:
            raise ValueError("effective cost requires a complete currency and unit basis")
        if self.cost_partial and self.effective_cost is None:
            raise ValueError("partial cost requires effective cost evidence")
        return self

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
    weight: int | None = Field(default=None, ge=1, le=100)
    paused: bool = False


class RoutingOrderMutation(RoutingModel):
    expected_version: int = Field(ge=0)
    binding_ids: tuple[UUID, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_bindings(self) -> RoutingOrderMutation:
        if len(self.binding_ids) != len(frozenset(self.binding_ids)):
            raise ValueError("binding_ids must not contain duplicates")
        return self


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
