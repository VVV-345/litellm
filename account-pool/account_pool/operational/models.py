"""定义不含凭证和上游正文的系统运行事件、关联事实及解析任务构造器。"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final, Literal, Self, assert_never
from uuid import UUID, uuid5

from pydantic import AwareDatetime, Field, model_validator

from account_pool.models import FrozenModel, ModelName

_OPERATIONAL_EVENT_NAMESPACE: Final = UUID("0bf35742-af58-4413-bac9-6c216914bd6c")
_SAFE_CODE_PATTERN: Final = r"^[a-z][a-z0-9_]{0,99}$"
_REQUEST_ID_PATTERN: Final = r"^[A-Za-z0-9._:-]+$"


class OperationalEventType(StrEnum):
    PARSER_TASK_COMPLETED = "parser_task_completed"
    PARSER_TASK_FAILED = "parser_task_failed"
    PARSER_TASK_INTERRUPTED = "parser_task_interrupted"


class OperationalEventSource(StrEnum):
    PARSER_TASK = "parser_task"


class OperationalEventOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class ParserTaskInterruptionSource(StrEnum):
    GRACEFUL_SHUTDOWN = "graceful_shutdown"
    STALE_HEARTBEAT = "stale_heartbeat"


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


OperationalEventDetails = Annotated[
    ParserTaskCompletedDetails | ParserTaskFailedDetails | ParserTaskInterruptedDetails,
    Field(discriminator="kind"),
]


class OperationalPoolEvent(FrozenModel):
    event_id: UUID
    event_type: OperationalEventType
    occurred_at: AwareDatetime
    channel_id: UUID
    model_id: ModelName | None = None
    deployment_id: str | None = None
    request_id: str = Field(min_length=1, max_length=128, pattern=_REQUEST_ID_PATTERN)
    lease_id: str | None = None
    reason_code: str | None = Field(default=None, min_length=1, max_length=100)
    actor_type: Literal["system"] = "system"
    actor_id: Literal["account_pool_parser_task"] = "account_pool_parser_task"
    safe_details: OperationalEventDetails

    @model_validator(mode="after")
    def validate_event_details(self) -> Self:
        if self.event_type.value != self.safe_details.kind:
            raise ValueError("event type must match safe details")
        if (self.event_type == OperationalEventType.PARSER_TASK_FAILED) != (self.reason_code is not None):
            raise ValueError("only failed parser task events require a reason code")
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
        if self.operational.source != OperationalEventSource.PARSER_TASK:
            raise ValueError("parser task events require the parser task source")
        if self.operational.operation_id != self.event.safe_details.task_id:
            raise ValueError("operation ID must match the parser task ID")
        if _event_outcome(self.event.event_type) != self.operational.outcome:
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
            request_id=request_id,
            reason_code=failure_code,
            safe_details=details,
        ),
        operational=OperationalEventFact(
            event_id=event_id,
            source=OperationalEventSource.PARSER_TASK,
            operation_id=task_id,
            outcome=_event_outcome(event_type),
        ),
    )


def _event_outcome(event_type: OperationalEventType) -> OperationalEventOutcome:
    match event_type:
        case OperationalEventType.PARSER_TASK_COMPLETED:
            return OperationalEventOutcome.SUCCEEDED
        case OperationalEventType.PARSER_TASK_FAILED:
            return OperationalEventOutcome.FAILED
        case OperationalEventType.PARSER_TASK_INTERRUPTED:
            return OperationalEventOutcome.INTERRUPTED
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
    if event_type == OperationalEventType.PARSER_TASK_INTERRUPTED and failure_code is None and interruption_source is not None:
        return ParserTaskInterruptedDetails(
            task_id=task_id,
            parser_run_id=parser_run_id,
            provider_id=provider_id,
            interruption_source=interruption_source,
        )
    raise ValueError("interrupted parser tasks require only an interruption source")
