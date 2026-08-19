"""定义主动探测、被动请求健康事实及最近活动时间的脱敏领域模型。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Final, Literal, Self
from uuid import UUID, uuid5

from pydantic import AwareDatetime, Field, model_validator

from account_pool.eligibility import EligibilityScope
from account_pool.health.settlement import HealthTransitionAction, SettlementHealthTransition, classify_settlement
from account_pool.models import AccountConfig, FrozenModel, Lease, ModelName, SettleRequest

_HEALTH_EVENT_NAMESPACE: Final = UUID("9d46edb1-8f4a-41b7-87c8-e558aac64456")
_SAFE_CODE_PATTERN: Final = r"^[a-z][a-z0-9_]{0,99}$"
_REQUEST_ID_PATTERN: Final = r"^[A-Za-z0-9._:-]+$"


class HealthProbeTrigger(StrEnum):
    MANUAL = "manual"
    INITIAL = "initial"
    HALF_OPEN = "half_open"
    IDLE = "idle"


class HealthProbeStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class HealthProbeRequest(FrozenModel):
    deployment_id: str | None = Field(default=None, min_length=1)


class HealthProbeResult(FrozenModel):
    probe_id: UUID
    status: HealthProbeStatus
    trigger: HealthProbeTrigger
    channel_id: UUID | None = None
    account_id: str | None = None
    deployment_id: str | None = None
    public_model: str | None = None
    reason_code: str | None = Field(default=None, pattern=_SAFE_CODE_PATTERN)
    response_status_code: int | None = Field(default=None, ge=100, le=599)
    latency_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if (self.status == HealthProbeStatus.SUCCEEDED) == (self.reason_code is not None):
            raise ValueError("only unsuccessful health probes require a reason code")
        if self.status == HealthProbeStatus.SUCCEEDED and self.deployment_id is None:
            raise ValueError("successful health probes require a deployment")
        return self


class HealthEventType(StrEnum):
    PASSIVE_RESULT = "passive_health_result"
    ACTIVE_PROBE_RESULT = "active_health_probe_result"


class HealthObservationSource(StrEnum):
    PASSIVE_REQUEST = "passive_request"
    ACTIVE_PROBE = "active_probe"


class HealthObservationOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PassiveHealthDetails(FrozenModel):
    kind: Literal["passive_health_result"] = "passive_health_result"
    outcome: HealthObservationOutcome
    transition: HealthTransitionAction
    response_status_code: int | None = Field(default=None, ge=100, le=599)
    latency_ms: float | None = Field(default=None, ge=0)


class ActiveProbeHealthDetails(FrozenModel):
    kind: Literal["active_health_probe_result"] = "active_health_probe_result"
    outcome: HealthObservationOutcome
    transition: HealthTransitionAction
    trigger: HealthProbeTrigger
    response_status_code: int | None = Field(default=None, ge=100, le=599)
    latency_ms: float = Field(ge=0)


HealthEventDetails = Annotated[
    PassiveHealthDetails | ActiveProbeHealthDetails,
    Field(discriminator="kind"),
]


class HealthPoolEvent(FrozenModel):
    event_id: UUID
    event_type: HealthEventType
    occurred_at: AwareDatetime
    channel_id: UUID | None = None
    model_id: ModelName
    deployment_id: str = Field(min_length=1, max_length=255)
    request_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=_REQUEST_ID_PATTERN)
    lease_id: str | None = Field(default=None, min_length=1, max_length=255)
    reason_code: str | None = Field(default=None, pattern=_SAFE_CODE_PATTERN)
    actor_type: Literal["system"] = "system"
    actor_id: str = Field(min_length=1, max_length=255)
    safe_details: HealthEventDetails

    @model_validator(mode="after")
    def validate_event_details(self) -> Self:
        if self.event_type.value != self.safe_details.kind:
            raise ValueError("event type must match safe details")
        return self


class HealthEventFact(FrozenModel):
    event_id: UUID
    account_id: str = Field(min_length=1, max_length=255)
    source: HealthObservationSource
    outcome: HealthObservationOutcome
    transition: HealthTransitionAction
    scope: EligibilityScope
    retry_at: AwareDatetime | None = None
    probe_trigger: HealthProbeTrigger | None = None

    @model_validator(mode="after")
    def validate_probe_trigger(self) -> Self:
        if (self.source == HealthObservationSource.ACTIVE_PROBE) != (self.probe_trigger is not None):
            raise ValueError("only active probe facts require a probe trigger")
        return self


class HealthEventRecord(FrozenModel):
    event: HealthPoolEvent
    health: HealthEventFact

    @model_validator(mode="after")
    def validate_linked_facts(self) -> Self:
        if self.event.event_id != self.health.event_id:
            raise ValueError("common event and health fact must share an event ID")
        if (self.event.event_type == HealthEventType.ACTIVE_PROBE_RESULT) != (
            self.health.source == HealthObservationSource.ACTIVE_PROBE
        ):
            raise ValueError("event type must match health source")
        return self


class HealthRequestActivity(FrozenModel):
    channel_id: UUID | None = None
    account_id: str = Field(min_length=1, max_length=255)
    model_id: ModelName
    deployment_id: str = Field(min_length=1, max_length=255)
    observed_at: AwareDatetime


class HealthActivity(FrozenModel):
    channel_id: UUID | None = None
    account_id: str = Field(min_length=1, max_length=255)
    model_id: ModelName
    deployment_id: str = Field(min_length=1, max_length=255)
    last_request_at: AwareDatetime | None = None
    last_success_at: AwareDatetime | None = None
    last_failure_at: AwareDatetime | None = None
    last_probe_at: AwareDatetime | None = None
    last_probe_success_at: AwareDatetime | None = None
    last_probe_failure_at: AwareDatetime | None = None
    updated_at: AwareDatetime


def passive_health_event_id(lease_id: str) -> UUID:
    return uuid5(_HEALTH_EVENT_NAMESPACE, f"passive:{lease_id}")


def equivalent_health_records(left: HealthEventRecord, right: HealthEventRecord) -> bool:
    normalized_right_event: Final = right.event.model_copy(update={"occurred_at": left.event.occurred_at})
    normalized_right_fact: Final = right.health.model_copy(update={"retry_at": left.health.retry_at})
    return left.event == normalized_right_event and left.health == normalized_right_fact


def build_passive_health_record(
    *,
    account: AccountConfig,
    lease: Lease,
    request: SettleRequest,
    occurred_at: AwareDatetime,
    scope: EligibilityScope,
) -> HealthEventRecord:
    transition: Final = classify_settlement(request, occurred_at.timestamp())
    outcome: Final = (
        HealthObservationOutcome.SUCCEEDED if request.success else HealthObservationOutcome.FAILED
    )
    event_id: Final = passive_health_event_id(lease.lease_id)
    return HealthEventRecord(
        event=HealthPoolEvent(
            event_id=event_id,
            event_type=HealthEventType.PASSIVE_RESULT,
            occurred_at=occurred_at,
            channel_id=account.channel_id,
            model_id=lease.public_model,
            deployment_id=lease.deployment_id,
            request_id=lease.request_id,
            lease_id=lease.lease_id,
            reason_code=transition.reason_code,
            actor_id="account_pool_gateway",
            safe_details=PassiveHealthDetails(
                outcome=outcome,
                transition=transition.action,
                response_status_code=request.status_code,
                latency_ms=request.latency_ms,
            ),
        ),
        health=HealthEventFact(
            event_id=event_id,
            account_id=account.id,
            source=HealthObservationSource.PASSIVE_REQUEST,
            outcome=outcome,
            transition=transition.action,
            scope=scope,
            retry_at=_retry_at(transition, occurred_at),
        ),
    )


def build_active_probe_record(
    *,
    result: HealthProbeResult,
    transition: SettlementHealthTransition,
    occurred_at: AwareDatetime,
) -> HealthEventRecord | None:
    if result.account_id is None or result.deployment_id is None or result.public_model is None:
        return None
    outcome: Final = (
        HealthObservationOutcome.SUCCEEDED
        if result.status == HealthProbeStatus.SUCCEEDED
        else HealthObservationOutcome.FAILED
    )
    return HealthEventRecord(
        event=HealthPoolEvent(
            event_id=result.probe_id,
            event_type=HealthEventType.ACTIVE_PROBE_RESULT,
            occurred_at=occurred_at,
            channel_id=result.channel_id,
            model_id=result.public_model,
            deployment_id=result.deployment_id,
            reason_code=result.reason_code,
            actor_id="account_pool_health_probe",
            safe_details=ActiveProbeHealthDetails(
                outcome=outcome,
                transition=transition.action,
                trigger=result.trigger,
                response_status_code=result.response_status_code,
                latency_ms=result.latency_ms,
            ),
        ),
        health=HealthEventFact(
            event_id=result.probe_id,
            account_id=result.account_id,
            source=HealthObservationSource.ACTIVE_PROBE,
            outcome=outcome,
            transition=transition.action,
            scope=EligibilityScope.DEPLOYMENT,
            retry_at=_retry_at(transition, occurred_at),
            probe_trigger=result.trigger,
        ),
    )


def _retry_at(transition: SettlementHealthTransition, occurred_at: AwareDatetime) -> AwareDatetime | None:
    if transition.cooldown_until is None:
        return None
    return datetime.fromtimestamp(transition.cooldown_until, tz=occurred_at.tzinfo)
