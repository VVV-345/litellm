"""定义统一事件日志的筛选条件、脱敏条目、游标分页和失败契约。"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from account_pool.audit.models import AuditOutcome
from account_pool.auth.actor import ActorAction
from account_pool.eligibility import EligibilityScope
from account_pool.health.models import (
    HealthObservationOutcome,
    HealthObservationSource,
    HealthProbeTrigger,
)
from account_pool.health.settlement import HealthTransitionAction
from account_pool.models import FrozenModel, ModelName


class EventQueryOutcome(StrEnum):
    ACCEPTED = "accepted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class EventQuery(FrozenModel):
    occurred_after: AwareDatetime | None = None
    occurred_before: AwareDatetime | None = None
    channel_id: UUID | None = None
    model_id: ModelName | None = None
    event_type: str | None = Field(default=None, min_length=1, max_length=100)
    health_outcome: HealthObservationOutcome | None = None
    health_transition: HealthTransitionAction | None = None
    reason_code: str | None = Field(default=None, min_length=1, max_length=100)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    outcome: EventQueryOutcome | None = None
    cursor: str | None = Field(default=None, min_length=1, max_length=1024)
    limit: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def validate_time_range(self) -> EventQuery:
        if (
            self.occurred_after is not None
            and self.occurred_before is not None
            and self.occurred_after > self.occurred_before
        ):
            raise ValueError("occurred_after must not be later than occurred_before")
        return self


class EventAuditSummary(FrozenModel):
    operation_id: UUID | None = None
    actor_role: Literal["proxy_admin", "system"]
    actor_action: ActorAction
    actor_envelope_id: UUID
    outcome: AuditOutcome


class EventHealthSummary(FrozenModel):
    account_id: str = Field(min_length=1)
    source: HealthObservationSource
    outcome: HealthObservationOutcome
    transition: HealthTransitionAction
    scope: EligibilityScope
    retry_at: AwareDatetime | None = None
    probe_trigger: HealthProbeTrigger | None = None


class EventOperationalSummary(FrozenModel):
    source: Literal["parser_task"]
    operation_id: UUID
    outcome: Literal["succeeded", "failed", "interrupted"]


class EventLogEntry(FrozenModel):
    event_id: UUID
    event_type: str = Field(min_length=1, max_length=100)
    occurred_at: AwareDatetime
    channel_id: UUID | None = None
    model_id: ModelName | None = None
    deployment_id: str | None = None
    request_id: str | None = None
    lease_id: str | None = None
    reason_code: str | None = None
    actor_type: Literal["user", "system"]
    actor_id: str = Field(min_length=1)
    outcome: EventQueryOutcome
    safe_details: JsonValue
    audit: EventAuditSummary | None = None
    health: EventHealthSummary | None = None
    operational: EventOperationalSummary | None = None

    @model_validator(mode="after")
    def validate_domain_fact(self) -> EventLogEntry:
        if sum(fact is not None for fact in (self.audit, self.health, self.operational)) != 1:
            raise ValueError("an event log entry requires exactly one linked domain fact")
        return self


class EventLogPage(FrozenModel):
    status: Literal["loaded"] = "loaded"
    events: tuple[EventLogEntry, ...] = ()
    next_cursor: str | None = None


class EventLogFailureCode(StrEnum):
    INVALID_CURSOR = "invalid_cursor"
    INVALID_STORED_DATA = "invalid_stored_data"
    DATABASE_UNAVAILABLE = "database_unavailable"


class EventLogFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: EventLogFailureCode
    retryable: bool


EventLogResult = EventLogPage | EventLogFailure
