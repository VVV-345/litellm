"""定义资格排除的范围、来源、状态和不可变数据模型。"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from account_pool.models import FrozenModel


class EligibilityScope(StrEnum):
    CHANNEL = "channel"
    MODEL = "model"
    DEPLOYMENT = "deployment"
    BILLING_ROUTE = "billing_route"


class EligibilitySource(StrEnum):
    HEALTH = "health"
    RESTRICTION = "restriction"
    CAPACITY = "capacity"


class EligibilityState(StrEnum):
    ACTIVE = "active"
    HALF_OPEN = "half_open"
    CLEARED = "cleared"


@dataclass(frozen=True, slots=True)
class EligibilitySubject:
    scope: EligibilityScope
    account_id: str
    model: str | None = None
    deployment_id: str | None = None
    billing_route_id: str | None = None


class EligibilityExclusion(FrozenModel):
    scope: EligibilityScope
    source: EligibilitySource
    account_id: str = Field(min_length=1)
    model: str | None = Field(default=None, min_length=1)
    deployment_id: str | None = Field(default=None, min_length=1)
    billing_route_id: str | None = Field(default=None, min_length=1)
    reason_code: str = Field(min_length=1, max_length=100)
    starts_at: float = Field(ge=0)
    retry_at: float | None = Field(default=None, ge=0)
    state: EligibilityState = EligibilityState.ACTIVE

    @model_validator(mode="after")
    def validate_scope_subject(self) -> Self:
        if self.scope == EligibilityScope.CHANNEL and any(
            subject is not None for subject in (self.model, self.deployment_id, self.billing_route_id)
        ):
            raise ValueError("channel scope forbids model, deployment, and billing route subjects")
        if self.scope == EligibilityScope.MODEL and (
            self.model is None or self.deployment_id is not None or self.billing_route_id is not None
        ):
            raise ValueError("model scope requires only a model subject")
        if self.scope == EligibilityScope.DEPLOYMENT and (
            self.model is None or self.deployment_id is None or self.billing_route_id is not None
        ):
            raise ValueError("deployment scope requires model and deployment subjects")
        if self.scope == EligibilityScope.BILLING_ROUTE and any(
            subject is None for subject in (self.model, self.deployment_id, self.billing_route_id)
        ):
            raise ValueError("billing route scope requires model, deployment, and billing route subjects")
        if self.retry_at is not None and self.retry_at < self.starts_at:
            raise ValueError("retry_at cannot precede starts_at")
        return self
