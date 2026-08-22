"""定义不含凭证和上游正文的系统运行事件、关联事实及解析任务构造器。"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import Annotated, Final, Literal, Self, assert_never
from uuid import UUID, uuid5

from pydantic import AwareDatetime, Field, model_validator

from account_pool.models import AcquireCandidateRejection, FrozenModel, Lease, ModelName, SettleRequest
from account_pool.operational.public_metadata_models import (
    PublicMetadataOperationalDetails,
    PublicMetadataTaskCompletedDetails,
    PublicMetadataTaskFailedDetails,
    PublicMetadataTaskRetryScheduledDetails,
)
from account_pool.operational.restriction_models import (
    RestrictionActivatedDetails,
    RestrictionClearedDetails,
    RestrictionEventDetails,
    RestrictionUpdatedDetails,
)

_OPERATIONAL_EVENT_NAMESPACE: Final = UUID("0bf35742-af58-4413-bac9-6c216914bd6c")
_SAFE_CODE_PATTERN: Final = r"^[a-z][a-z0-9_]{0,99}$"
_REQUEST_ID_PATTERN: Final = r"^[A-Za-z0-9._:-]+$"
_REQUEST_ID_RE: Final = re.compile(_REQUEST_ID_PATTERN)


class OperationalEventType(StrEnum):
    PARSER_TASK_COMPLETED = "parser_task_completed"
    PARSER_TASK_FAILED = "parser_task_failed"
    PARSER_TASK_INTERRUPTED = "parser_task_interrupted"
    PARSER_SNAPSHOT_EXPORTED = "parser_snapshot_exported"
    PARSER_SNAPSHOT_EXPORT_RETRY_SCHEDULED = "parser_snapshot_export_retry_scheduled"
    PARSER_SNAPSHOT_EXPORT_FAILED = "parser_snapshot_export_failed"
    SYNC_RETRY_SUCCEEDED = "sync_retry_succeeded"
    SYNC_RETRY_FAILED = "sync_retry_failed"
    SYNC_RETRY_DEFERRED = "sync_retry_deferred"
    REQUEST_ACQUIRED = "request_acquired"
    REQUEST_ACQUIRE_FAILED = "request_acquire_failed"
    REQUEST_SETTLED = "request_settled"
    REQUEST_USAGE_RECORDED = "request_usage_recorded"
    REQUEST_RELEASED = "request_released"
    LEASE_EXPIRED = "lease_expired"
    ELIGIBILITY_RESTRICTION_ACTIVATED = "eligibility_restriction_activated"
    ELIGIBILITY_RESTRICTION_UPDATED = "eligibility_restriction_updated"
    ELIGIBILITY_RESTRICTION_CLEARED = "eligibility_restriction_cleared"
    PUBLIC_METADATA_TASK_COMPLETED = "public_metadata_task_completed"
    PUBLIC_METADATA_TASK_RETRY_SCHEDULED = "public_metadata_task_retry_scheduled"
    PUBLIC_METADATA_TASK_FAILED = "public_metadata_task_failed"


class OperationalEventSource(StrEnum):
    PARSER_TASK = "parser_task"
    PARSER_SNAPSHOT_EXPORT = "parser_snapshot_export"
    SYNC_RECONCILE = "sync_reconcile"
    REQUEST_LIFECYCLE = "request_lifecycle"
    ELIGIBILITY_TRANSITION = "eligibility_transition"
    PUBLIC_METADATA_TASK = "public_metadata_task"


class OperationalEventOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class ParserTaskInterruptionSource(StrEnum):
    GRACEFUL_SHUTDOWN = "graceful_shutdown"
    STALE_HEARTBEAT = "stale_heartbeat"


class ParserSnapshotExportTrigger(StrEnum):
    INITIAL = "initial"
    RETRY = "retry"


class ParserTaskCompletedDetails(FrozenModel):
    kind: Literal["parser_task_completed"] = "parser_task_completed"
    task_id: UUID
    parser_run_id: UUID
    provider_id: str = Field(min_length=1, max_length=100)


class ParserTaskFailedDetails(FrozenModel):
    kind: Literal["parser_task_failed"] = "parser_task_failed"
    task_id: UUID
    parser_run_id: UUID
    provider_id: str = Field(min_length=1, max_length=100)
    failure_code: str = Field(pattern=_SAFE_CODE_PATTERN)


class ParserTaskInterruptedDetails(FrozenModel):
    kind: Literal["parser_task_interrupted"] = "parser_task_interrupted"
    task_id: UUID
    parser_run_id: UUID
    provider_id: str = Field(min_length=1, max_length=100)
    interruption_source: ParserTaskInterruptionSource


class ParserSnapshotExportedDetails(FrozenModel):
    kind: Literal["parser_snapshot_exported"] = "parser_snapshot_exported"
    parser_run_id: UUID
    attempt_count: int = Field(ge=1)
    trigger: ParserSnapshotExportTrigger


class ParserSnapshotExportRetryScheduledDetails(FrozenModel):
    kind: Literal["parser_snapshot_export_retry_scheduled"] = "parser_snapshot_export_retry_scheduled"
    parser_run_id: UUID
    attempt_count: int = Field(ge=1)
    trigger: ParserSnapshotExportTrigger
    failure_code: str = Field(pattern=_SAFE_CODE_PATTERN)


class ParserSnapshotExportFailedDetails(FrozenModel):
    kind: Literal["parser_snapshot_export_failed"] = "parser_snapshot_export_failed"
    parser_run_id: UUID
    attempt_count: int = Field(ge=1)
    trigger: ParserSnapshotExportTrigger
    failure_code: str = Field(pattern=_SAFE_CODE_PATTERN)


class SyncRetrySucceededDetails(FrozenModel):
    kind: Literal["sync_retry_succeeded"] = "sync_retry_succeeded"
    operation_id: UUID
    sync_action: str = Field(pattern=_SAFE_CODE_PATTERN)
    attempt_count: int = Field(ge=1)


class SyncRetryFailedDetails(FrozenModel):
    kind: Literal["sync_retry_failed"] = "sync_retry_failed"
    operation_id: UUID
    sync_action: str = Field(pattern=_SAFE_CODE_PATTERN)
    attempt_count: int = Field(ge=1)
    failure_code: str = Field(pattern=_SAFE_CODE_PATTERN)


class SyncRetryDeferredDetails(FrozenModel):
    kind: Literal["sync_retry_deferred"] = "sync_retry_deferred"
    operation_id: UUID
    sync_action: str = Field(pattern=_SAFE_CODE_PATTERN)
    attempt_count: int = Field(ge=0)
    reason_code: str = Field(pattern=_SAFE_CODE_PATTERN)


class RequestAcquiredDetails(FrozenModel):
    kind: Literal["request_acquired"] = "request_acquired"
    estimated_tokens: int = Field(ge=0)
    billing_route_id: str | None = None


class RequestAcquireFailedDetails(FrozenModel):
    kind: Literal["request_acquire_failed"] = "request_acquire_failed"
    rejection_stage: str = Field(pattern=_SAFE_CODE_PATTERN)
    rejection_scope: str = Field(pattern=_SAFE_CODE_PATTERN)
    rejection_source: str = Field(pattern=_SAFE_CODE_PATTERN)
    retry_at: float | None = Field(default=None, ge=0)


class RequestSettledDetails(FrozenModel):
    kind: Literal["request_settled"] = "request_settled"
    applied: bool
    success: bool
    status_code: int | None = Field(default=None, ge=100, le=599)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    latency_ms: float | None = Field(default=None, ge=0)


class RequestReleasedDetails(FrozenModel):
    kind: Literal["request_released"] = "request_released"
    released: bool
    settled: bool


class RequestUsageRecordedDetails(FrozenModel):
    kind: Literal["request_usage_recorded"] = "request_usage_recorded"
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float | None = Field(default=None, ge=0)


class LeaseExpiredDetails(FrozenModel):
    kind: Literal["lease_expired"] = "lease_expired"
    settled: bool


OperationalEventDetails = Annotated[
    ParserTaskCompletedDetails
    | ParserTaskFailedDetails
    | ParserTaskInterruptedDetails
    | ParserSnapshotExportedDetails
    | ParserSnapshotExportRetryScheduledDetails
    | ParserSnapshotExportFailedDetails
    | SyncRetrySucceededDetails
    | SyncRetryFailedDetails
    | SyncRetryDeferredDetails
    | RequestAcquiredDetails
    | RequestAcquireFailedDetails
    | RequestSettledDetails
    | RequestUsageRecordedDetails
    | RequestReleasedDetails
    | LeaseExpiredDetails
    | RestrictionEventDetails
    | PublicMetadataOperationalDetails,
    Field(discriminator="kind"),
]


class OperationalPoolEvent(FrozenModel):
    event_id: UUID
    event_type: OperationalEventType
    occurred_at: AwareDatetime
    channel_id: UUID
    model_id: ModelName | None = None
    deployment_id: str | None = None
    request_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=_REQUEST_ID_PATTERN)
    lease_id: str | None = None
    reason_code: str | None = Field(default=None, min_length=1, max_length=100)
    actor_type: Literal["system"] = "system"
    actor_id: Literal[
        "account_pool_parser_task",
        "account_pool_parser_snapshot",
        "account_pool_reconciler",
        "account_pool_scheduler",
        "account_pool_state_store",
        "account_pool_lease_reaper",
        "account_pool_eligibility",
        "account_pool_public_metadata",
    ]
    safe_details: OperationalEventDetails

    @model_validator(mode="after")
    def validate_event_details(self) -> Self:
        if self.event_type.value != self.safe_details.kind:
            raise ValueError("event type must match safe details")
        requires_reason: Final = self.event_type in (
            OperationalEventType.PARSER_TASK_FAILED,
            OperationalEventType.PARSER_SNAPSHOT_EXPORT_RETRY_SCHEDULED,
            OperationalEventType.PARSER_SNAPSHOT_EXPORT_FAILED,
            OperationalEventType.SYNC_RETRY_FAILED,
            OperationalEventType.SYNC_RETRY_DEFERRED,
            OperationalEventType.REQUEST_ACQUIRE_FAILED,
            OperationalEventType.LEASE_EXPIRED,
            OperationalEventType.ELIGIBILITY_RESTRICTION_ACTIVATED,
            OperationalEventType.ELIGIBILITY_RESTRICTION_UPDATED,
            OperationalEventType.ELIGIBILITY_RESTRICTION_CLEARED,
            OperationalEventType.PUBLIC_METADATA_TASK_RETRY_SCHEDULED,
            OperationalEventType.PUBLIC_METADATA_TASK_FAILED,
        )
        failed_settlement: Final = (
            self.event_type == OperationalEventType.REQUEST_SETTLED
            and isinstance(self.safe_details, RequestSettledDetails)
            and (not self.safe_details.applied or not self.safe_details.success)
        )
        failed_release: Final = (
            self.event_type == OperationalEventType.REQUEST_RELEASED
            and isinstance(self.safe_details, RequestReleasedDetails)
            and not self.safe_details.released
        )
        if (requires_reason or failed_settlement or failed_release) != (self.reason_code is not None):
            raise ValueError("event reason code does not match its outcome")
        return self


class OperationalEventFact(FrozenModel):
    event_id: UUID
    source: OperationalEventSource
    operation_id: UUID
    outcome: OperationalEventOutcome


class OperationalEventRecord(FrozenModel):
    event: OperationalPoolEvent
    operational: OperationalEventFact

    @model_validator(mode="after")
    def validate_linked_facts(self) -> Self:
        if self.event.event_id != self.operational.event_id:
            raise ValueError("common event and operational fact must share an event ID")
        if _event_source(self.event.event_type) != self.operational.source:
            raise ValueError("event type must match operational source")
        if self.operational.operation_id != _event_operation_id(self.event):
            raise ValueError("operation ID must match event details")
        if (
            _event_outcome(self.event.event_type, _request_outcome_details(self.event.safe_details))
            != self.operational.outcome
        ):
            raise ValueError("event type must match operational outcome")
        return self


def build_parser_task_operational_record(
    *,
    task_id: UUID,
    channel_id: UUID,
    parser_run_id: UUID,
    provider_id: str,
    request_id: str,
    occurred_at: AwareDatetime,
    event_type: OperationalEventType,
    failure_code: str | None = None,
    interruption_source: ParserTaskInterruptionSource | None = None,
) -> OperationalEventRecord:
    details: Final = _parser_task_details(
        event_type=event_type,
        task_id=task_id,
        parser_run_id=parser_run_id,
        provider_id=provider_id,
        failure_code=failure_code,
        interruption_source=interruption_source,
    )
    event_id: Final = uuid5(_OPERATIONAL_EVENT_NAMESPACE, f"{task_id}:{event_type.value}")
    return OperationalEventRecord(
        event=OperationalPoolEvent(
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            channel_id=channel_id,
            request_id=_safe_request_id(request_id),
            reason_code=failure_code,
            actor_id="account_pool_parser_task",
            safe_details=details,
        ),
        operational=OperationalEventFact(
            event_id=event_id,
            source=OperationalEventSource.PARSER_TASK,
            operation_id=task_id,
            outcome=_event_outcome(event_type),
        ),
    )


def build_parser_snapshot_export_record(
    *,
    channel_id: UUID,
    parser_run_id: UUID,
    occurred_at: AwareDatetime,
    event_type: OperationalEventType,
    attempt_count: int,
    trigger: ParserSnapshotExportTrigger,
    failure_code: str | None = None,
) -> OperationalEventRecord:
    details: Final = _snapshot_export_details(
        event_type=event_type,
        parser_run_id=parser_run_id,
        attempt_count=attempt_count,
        trigger=trigger,
        failure_code=failure_code,
    )
    event_id: Final = uuid5(
        _OPERATIONAL_EVENT_NAMESPACE,
        f"snapshot:{parser_run_id}:{attempt_count}:{event_type.value}",
    )
    return OperationalEventRecord(
        event=OperationalPoolEvent(
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            channel_id=channel_id,
            reason_code=failure_code,
            actor_id="account_pool_parser_snapshot",
            safe_details=details,
        ),
        operational=OperationalEventFact(
            event_id=event_id,
            source=OperationalEventSource.PARSER_SNAPSHOT_EXPORT,
            operation_id=parser_run_id,
            outcome=_event_outcome(event_type),
        ),
    )


def build_sync_reconcile_record(
    *,
    operation_id: UUID,
    channel_id: UUID,
    sync_action: str,
    attempt_count: int,
    occurred_at: AwareDatetime,
    event_type: OperationalEventType,
    reason_code: str | None = None,
) -> OperationalEventRecord:
    details: Final = _sync_retry_details(
        event_type=event_type,
        operation_id=operation_id,
        sync_action=sync_action,
        attempt_count=attempt_count,
        reason_code=reason_code,
    )
    event_id: Final = uuid5(
        _OPERATIONAL_EVENT_NAMESPACE,
        f"sync:{operation_id}:{attempt_count}:{event_type.value}",
    )
    return OperationalEventRecord(
        event=OperationalPoolEvent(
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            channel_id=channel_id,
            request_id=f"reconcile:{operation_id.hex}:{attempt_count}",
            reason_code=reason_code,
            actor_id="account_pool_reconciler",
            safe_details=details,
        ),
        operational=OperationalEventFact(
            event_id=event_id,
            source=OperationalEventSource.SYNC_RECONCILE,
            operation_id=operation_id,
            outcome=_event_outcome(event_type),
        ),
    )


def build_request_acquired_record(
    *,
    channel_id: UUID,
    lease: Lease,
    estimated_tokens: int,
    occurred_at: AwareDatetime,
) -> OperationalEventRecord:
    return _request_record(
        channel_id=channel_id,
        lease=lease,
        occurred_at=occurred_at,
        event_type=OperationalEventType.REQUEST_ACQUIRED,
        details=RequestAcquiredDetails(
            estimated_tokens=estimated_tokens,
            billing_route_id=lease.billing_route_id,
        ),
        actor_id="account_pool_scheduler",
    )


def build_request_acquire_failed_record(
    *,
    channel_id: UUID,
    model_id: ModelName,
    request_id: str,
    rejection: AcquireCandidateRejection,
    occurred_at: AwareDatetime,
) -> OperationalEventRecord:
    safe_request_id: Final = _safe_request_id(request_id)
    operation_id: Final = uuid5(
        _OPERATIONAL_EVENT_NAMESPACE,
        f"request:{safe_request_id}:{rejection.deployment_id}:{rejection.reason_code}",
    )
    event_id: Final = uuid5(
        _OPERATIONAL_EVENT_NAMESPACE,
        f"{operation_id}:request_acquire_failed:{occurred_at.isoformat()}",
    )
    return OperationalEventRecord(
        event=OperationalPoolEvent(
            event_id=event_id,
            event_type=OperationalEventType.REQUEST_ACQUIRE_FAILED,
            occurred_at=occurred_at,
            channel_id=channel_id,
            model_id=model_id,
            deployment_id=rejection.deployment_id,
            request_id=safe_request_id,
            reason_code=rejection.reason_code,
            actor_id="account_pool_scheduler",
            safe_details=RequestAcquireFailedDetails(
                rejection_stage=rejection.stage.value,
                rejection_scope=rejection.scope.value,
                rejection_source=rejection.source.value,
                retry_at=rejection.retry_at,
            ),
        ),
        operational=OperationalEventFact(
            event_id=event_id,
            source=OperationalEventSource.REQUEST_LIFECYCLE,
            operation_id=operation_id,
            outcome=OperationalEventOutcome.FAILED,
        ),
    )


def build_request_settled_record(
    *,
    channel_id: UUID,
    lease: Lease,
    request: SettleRequest,
    applied: bool,
    occurred_at: AwareDatetime,
) -> OperationalEventRecord:
    reason_code: Final = (
        None if applied and request.success else "upstream_request_failed" if applied else "settlement_rejected"
    )
    return _request_record(
        channel_id=channel_id,
        lease=lease,
        occurred_at=occurred_at,
        event_type=OperationalEventType.REQUEST_SETTLED,
        details=RequestSettledDetails(
            applied=applied,
            success=request.success,
            status_code=request.status_code,
            input_tokens=request.input_tokens,
            output_tokens=request.output_tokens,
            cost_usd=request.cost_usd,
            latency_ms=request.latency_ms,
        ),
        actor_id="account_pool_state_store",
        reason_code=reason_code,
    )


def build_request_released_record(
    *,
    channel_id: UUID,
    lease: Lease,
    released: bool,
    occurred_at: AwareDatetime,
) -> OperationalEventRecord:
    reason_code: Final = None if released else "release_rejected"
    return _request_record(
        channel_id=channel_id,
        lease=lease,
        occurred_at=occurred_at,
        event_type=OperationalEventType.REQUEST_RELEASED,
        details=RequestReleasedDetails(released=released, settled=lease.settled),
        actor_id="account_pool_state_store",
        reason_code=reason_code,
    )


def build_request_usage_recorded_record(
    *,
    channel_id: UUID,
    lease: Lease,
    request: SettleRequest,
    occurred_at: AwareDatetime,
) -> OperationalEventRecord:
    return _request_record(
        channel_id=channel_id,
        lease=lease,
        occurred_at=occurred_at,
        event_type=OperationalEventType.REQUEST_USAGE_RECORDED,
        details=RequestUsageRecordedDetails(
            input_tokens=request.input_tokens,
            output_tokens=request.output_tokens,
            cost_usd=request.cost_usd,
        ),
        actor_id="account_pool_state_store",
    )


def build_lease_expired_record(
    *,
    channel_id: UUID,
    lease: Lease,
    occurred_at: AwareDatetime,
) -> OperationalEventRecord:
    return _request_record(
        channel_id=channel_id,
        lease=lease,
        occurred_at=occurred_at,
        event_type=OperationalEventType.LEASE_EXPIRED,
        details=LeaseExpiredDetails(settled=lease.settled),
        actor_id="account_pool_lease_reaper",
        reason_code="lease_expired",
    )


def _request_record(
    *,
    channel_id: UUID,
    lease: Lease,
    occurred_at: AwareDatetime,
    event_type: OperationalEventType,
    details: RequestAcquiredDetails
    | RequestSettledDetails
    | RequestUsageRecordedDetails
    | RequestReleasedDetails
    | LeaseExpiredDetails,
    actor_id: Literal["account_pool_scheduler", "account_pool_state_store", "account_pool_lease_reaper"],
    reason_code: str | None = None,
) -> OperationalEventRecord:
    operation_id: Final = uuid5(_OPERATIONAL_EVENT_NAMESPACE, f"lease:{lease.lease_id}")
    event_id: Final = uuid5(
        _OPERATIONAL_EVENT_NAMESPACE,
        f"{lease.lease_id}:{event_type.value}:{reason_code or 'succeeded'}",
    )
    return OperationalEventRecord(
        event=OperationalPoolEvent(
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            channel_id=channel_id,
            model_id=lease.public_model,
            deployment_id=lease.deployment_id,
            request_id=_safe_request_id(lease.request_id),
            lease_id=lease.lease_id,
            reason_code=reason_code,
            actor_id=actor_id,
            safe_details=details,
        ),
        operational=OperationalEventFact(
            event_id=event_id,
            source=OperationalEventSource.REQUEST_LIFECYCLE,
            operation_id=operation_id,
            outcome=_event_outcome(event_type, details),
        ),
    )


def _event_outcome(
    event_type: OperationalEventType,
    details: RequestAcquiredDetails
    | RequestSettledDetails
    | RequestUsageRecordedDetails
    | RequestReleasedDetails
    | LeaseExpiredDetails
    | None = None,
) -> OperationalEventOutcome:
    match event_type:
        case OperationalEventType.PARSER_TASK_COMPLETED:
            return OperationalEventOutcome.SUCCEEDED
        case OperationalEventType.PARSER_TASK_FAILED:
            return OperationalEventOutcome.FAILED
        case OperationalEventType.PARSER_TASK_INTERRUPTED:
            return OperationalEventOutcome.INTERRUPTED
        case OperationalEventType.PARSER_SNAPSHOT_EXPORTED:
            return OperationalEventOutcome.SUCCEEDED
        case (
            OperationalEventType.PARSER_SNAPSHOT_EXPORT_RETRY_SCHEDULED
            | OperationalEventType.PARSER_SNAPSHOT_EXPORT_FAILED
            | OperationalEventType.SYNC_RETRY_FAILED
        ):
            return OperationalEventOutcome.FAILED
        case OperationalEventType.SYNC_RETRY_SUCCEEDED:
            return OperationalEventOutcome.SUCCEEDED
        case OperationalEventType.SYNC_RETRY_DEFERRED:
            return OperationalEventOutcome.INTERRUPTED
        case OperationalEventType.REQUEST_ACQUIRED:
            return OperationalEventOutcome.SUCCEEDED
        case OperationalEventType.REQUEST_ACQUIRE_FAILED:
            return OperationalEventOutcome.FAILED
        case OperationalEventType.REQUEST_SETTLED:
            if not isinstance(details, RequestSettledDetails):
                raise ValueError("request settlement requires settlement details")
            return (
                OperationalEventOutcome.SUCCEEDED
                if details.applied and details.success
                else OperationalEventOutcome.FAILED
            )
        case OperationalEventType.REQUEST_USAGE_RECORDED:
            return OperationalEventOutcome.SUCCEEDED
        case OperationalEventType.REQUEST_RELEASED:
            if not isinstance(details, RequestReleasedDetails):
                raise ValueError("request release requires release details")
            return OperationalEventOutcome.SUCCEEDED if details.released else OperationalEventOutcome.FAILED
        case OperationalEventType.LEASE_EXPIRED:
            return OperationalEventOutcome.INTERRUPTED
        case (
            OperationalEventType.ELIGIBILITY_RESTRICTION_ACTIVATED
            | OperationalEventType.ELIGIBILITY_RESTRICTION_UPDATED
            | OperationalEventType.ELIGIBILITY_RESTRICTION_CLEARED
        ):
            return OperationalEventOutcome.SUCCEEDED
        case OperationalEventType.PUBLIC_METADATA_TASK_COMPLETED:
            return OperationalEventOutcome.SUCCEEDED
        case OperationalEventType.PUBLIC_METADATA_TASK_RETRY_SCHEDULED:
            return OperationalEventOutcome.INTERRUPTED
        case OperationalEventType.PUBLIC_METADATA_TASK_FAILED:
            return OperationalEventOutcome.FAILED
    assert_never(event_type)


def _parser_task_details(
    *,
    event_type: OperationalEventType,
    task_id: UUID,
    parser_run_id: UUID,
    provider_id: str,
    failure_code: str | None,
    interruption_source: ParserTaskInterruptionSource | None,
) -> OperationalEventDetails:
    if event_type == OperationalEventType.PARSER_TASK_COMPLETED:
        if failure_code is not None or interruption_source is not None:
            raise ValueError("completed parser tasks cannot have failure details")
        return ParserTaskCompletedDetails(
            task_id=task_id,
            parser_run_id=parser_run_id,
            provider_id=provider_id,
        )
    if event_type == OperationalEventType.PARSER_TASK_FAILED:
        if failure_code is None or interruption_source is not None:
            raise ValueError("failed parser tasks require only a failure code")
        return ParserTaskFailedDetails(
            task_id=task_id,
            parser_run_id=parser_run_id,
            provider_id=provider_id,
            failure_code=failure_code,
        )
    if (
        event_type == OperationalEventType.PARSER_TASK_INTERRUPTED
        and failure_code is None
        and interruption_source is not None
    ):
        return ParserTaskInterruptedDetails(
            task_id=task_id,
            parser_run_id=parser_run_id,
            provider_id=provider_id,
            interruption_source=interruption_source,
        )
    raise ValueError("interrupted parser tasks require only an interruption source")


def _snapshot_export_details(
    *,
    event_type: OperationalEventType,
    parser_run_id: UUID,
    attempt_count: int,
    trigger: ParserSnapshotExportTrigger,
    failure_code: str | None,
) -> OperationalEventDetails:
    if event_type == OperationalEventType.PARSER_SNAPSHOT_EXPORTED:
        if failure_code is not None:
            raise ValueError("successful snapshot exports cannot have a failure code")
        return ParserSnapshotExportedDetails(
            parser_run_id=parser_run_id,
            attempt_count=attempt_count,
            trigger=trigger,
        )
    if event_type == OperationalEventType.PARSER_SNAPSHOT_EXPORT_RETRY_SCHEDULED:
        if failure_code is None:
            raise ValueError("scheduled snapshot retries require a failure code")
        return ParserSnapshotExportRetryScheduledDetails(
            parser_run_id=parser_run_id,
            attempt_count=attempt_count,
            trigger=trigger,
            failure_code=failure_code,
        )
    if event_type == OperationalEventType.PARSER_SNAPSHOT_EXPORT_FAILED and failure_code is not None:
        return ParserSnapshotExportFailedDetails(
            parser_run_id=parser_run_id,
            attempt_count=attempt_count,
            trigger=trigger,
            failure_code=failure_code,
        )
    raise ValueError("permanent snapshot export failures require a failure code")


def _event_source(event_type: OperationalEventType) -> OperationalEventSource:
    if event_type in (
        OperationalEventType.PARSER_TASK_COMPLETED,
        OperationalEventType.PARSER_TASK_FAILED,
        OperationalEventType.PARSER_TASK_INTERRUPTED,
    ):
        return OperationalEventSource.PARSER_TASK
    if event_type in (
        OperationalEventType.PARSER_SNAPSHOT_EXPORTED,
        OperationalEventType.PARSER_SNAPSHOT_EXPORT_RETRY_SCHEDULED,
        OperationalEventType.PARSER_SNAPSHOT_EXPORT_FAILED,
    ):
        return OperationalEventSource.PARSER_SNAPSHOT_EXPORT
    if event_type in (
        OperationalEventType.SYNC_RETRY_SUCCEEDED,
        OperationalEventType.SYNC_RETRY_FAILED,
        OperationalEventType.SYNC_RETRY_DEFERRED,
    ):
        return OperationalEventSource.SYNC_RECONCILE
    if event_type in (
        OperationalEventType.REQUEST_ACQUIRED,
        OperationalEventType.REQUEST_ACQUIRE_FAILED,
        OperationalEventType.REQUEST_SETTLED,
        OperationalEventType.REQUEST_USAGE_RECORDED,
        OperationalEventType.REQUEST_RELEASED,
        OperationalEventType.LEASE_EXPIRED,
    ):
        return OperationalEventSource.REQUEST_LIFECYCLE
    if event_type in (
        OperationalEventType.ELIGIBILITY_RESTRICTION_ACTIVATED,
        OperationalEventType.ELIGIBILITY_RESTRICTION_UPDATED,
        OperationalEventType.ELIGIBILITY_RESTRICTION_CLEARED,
    ):
        return OperationalEventSource.ELIGIBILITY_TRANSITION
    if event_type in (
        OperationalEventType.PUBLIC_METADATA_TASK_COMPLETED,
        OperationalEventType.PUBLIC_METADATA_TASK_RETRY_SCHEDULED,
        OperationalEventType.PUBLIC_METADATA_TASK_FAILED,
    ):
        return OperationalEventSource.PUBLIC_METADATA_TASK
    assert_never(event_type)


def _event_operation_id(event: OperationalPoolEvent) -> UUID:
    details: Final = event.safe_details
    if isinstance(details, (ParserTaskCompletedDetails, ParserTaskFailedDetails, ParserTaskInterruptedDetails)):
        return details.task_id
    if isinstance(
        details,
        (ParserSnapshotExportedDetails, ParserSnapshotExportRetryScheduledDetails, ParserSnapshotExportFailedDetails),
    ):
        return details.parser_run_id
    if isinstance(details, (SyncRetrySucceededDetails, SyncRetryFailedDetails, SyncRetryDeferredDetails)):
        return details.operation_id
    if isinstance(details, RequestAcquireFailedDetails):
        return uuid5(
            _OPERATIONAL_EVENT_NAMESPACE,
            f"request:{event.request_id}:{event.deployment_id}:{event.reason_code}",
        )
    if isinstance(details, (RestrictionActivatedDetails, RestrictionUpdatedDetails, RestrictionClearedDetails)):
        return details.restriction_id
    if isinstance(
        details,
        (PublicMetadataTaskCompletedDetails, PublicMetadataTaskRetryScheduledDetails, PublicMetadataTaskFailedDetails),
    ):
        return details.task_id
    if event.lease_id is None:
        raise ValueError("lease lifecycle events require a lease ID")
    return uuid5(_OPERATIONAL_EVENT_NAMESPACE, f"lease:{event.lease_id}")


def _request_outcome_details(
    details: OperationalEventDetails,
) -> (
    RequestAcquiredDetails
    | RequestSettledDetails
    | RequestUsageRecordedDetails
    | RequestReleasedDetails
    | LeaseExpiredDetails
    | None
):
    if isinstance(
        details,
        (
            RequestAcquiredDetails,
            RequestSettledDetails,
            RequestUsageRecordedDetails,
            RequestReleasedDetails,
            LeaseExpiredDetails,
        ),
    ):
        return details
    return None


def _safe_request_id(request_id: str) -> str:
    if len(request_id) <= 128 and _REQUEST_ID_RE.fullmatch(request_id) is not None:
        return request_id
    digest: Final = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _sync_retry_details(
    *,
    event_type: OperationalEventType,
    operation_id: UUID,
    sync_action: str,
    attempt_count: int,
    reason_code: str | None,
) -> OperationalEventDetails:
    if event_type == OperationalEventType.SYNC_RETRY_SUCCEEDED:
        if reason_code is not None:
            raise ValueError("successful sync retries cannot have a reason code")
        return SyncRetrySucceededDetails(
            operation_id=operation_id,
            sync_action=sync_action,
            attempt_count=attempt_count,
        )
    if event_type == OperationalEventType.SYNC_RETRY_FAILED:
        if reason_code is None:
            raise ValueError("failed sync retries require a failure code")
        return SyncRetryFailedDetails(
            operation_id=operation_id,
            sync_action=sync_action,
            attempt_count=attempt_count,
            failure_code=reason_code,
        )
    if event_type == OperationalEventType.SYNC_RETRY_DEFERRED and reason_code is not None:
        return SyncRetryDeferredDetails(
            operation_id=operation_id,
            sync_action=sync_action,
            attempt_count=attempt_count,
            reason_code=reason_code,
        )
    raise ValueError("deferred sync retries require a reason code")
