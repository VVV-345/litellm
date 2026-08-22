"""定义资格限制生效、变化和解除事件的脱敏详情模型。"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from account_pool.eligibility import EligibilityScope, EligibilityState
from account_pool.models import FrozenModel


class RestrictionActivatedDetails(FrozenModel):
    kind: Literal["eligibility_restriction_activated"] = "eligibility_restriction_activated"
    restriction_id: UUID
    scope: EligibilityScope
    source: Literal["restriction"] = "restriction"
    state: EligibilityState
    billing_route_id: str | None = None
    starts_at: float = Field(ge=0)
    retry_at: float | None = Field(default=None, ge=0)


class RestrictionUpdatedDetails(FrozenModel):
    kind: Literal["eligibility_restriction_updated"] = "eligibility_restriction_updated"
    restriction_id: UUID
    scope: EligibilityScope
    source: Literal["restriction"] = "restriction"
    previous_state: EligibilityState
    state: EligibilityState
    billing_route_id: str | None = None
    starts_at: float = Field(ge=0)
    previous_retry_at: float | None = Field(default=None, ge=0)
    retry_at: float | None = Field(default=None, ge=0)


class RestrictionClearedDetails(FrozenModel):
    kind: Literal["eligibility_restriction_cleared"] = "eligibility_restriction_cleared"
    restriction_id: UUID
    scope: EligibilityScope
    source: Literal["restriction"] = "restriction"
    previous_state: EligibilityState
    billing_route_id: str | None = None
    starts_at: float = Field(ge=0)
    previous_retry_at: float | None = Field(default=None, ge=0)


RestrictionEventDetails = RestrictionActivatedDetails | RestrictionUpdatedDetails | RestrictionClearedDetails
