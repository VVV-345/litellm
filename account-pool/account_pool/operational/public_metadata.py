"""构造不含 URL、凭证或上游正文的公开元数据任务事件。"""

from typing import Final
from uuid import UUID, uuid5

from pydantic import AwareDatetime

from account_pool.operational.models import (
    OperationalEventFact,
    OperationalEventOutcome,
    OperationalEventRecord,
    OperationalEventSource,
    OperationalEventType,
    OperationalPoolEvent,
)
from account_pool.operational.public_metadata_models import (
    PublicMetadataOperationalDetails,
    PublicMetadataTaskCompletedDetails,
    PublicMetadataTaskFailedDetails,
    PublicMetadataTaskRetryScheduledDetails,
)

_PUBLIC_METADATA_EVENT_NAMESPACE: Final = UUID("a351a450-183f-4fab-a318-b4486f64e5db")


def build_public_metadata_task_record(
    *,
    task_id: UUID,
    channel_id: UUID,
    parser_run_id: UUID,
    provider_id: str,
    attempt_count: int,
    occurred_at: AwareDatetime,
    event_type: OperationalEventType,
    failure_code: str | None = None,
    next_attempt_at: AwareDatetime | None = None,
) -> OperationalEventRecord:
    details: Final = _details(
        event_type=event_type,
        task_id=task_id,
        parser_run_id=parser_run_id,
        provider_id=provider_id,
        attempt_count=attempt_count,
        failure_code=failure_code,
        next_attempt_at=next_attempt_at,
    )
    event_id: Final = uuid5(
        _PUBLIC_METADATA_EVENT_NAMESPACE,
        f"{task_id}:{attempt_count}:{event_type.value}",
    )
    return OperationalEventRecord(
        event=OperationalPoolEvent(
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            channel_id=channel_id,
            reason_code=failure_code,
            actor_id="account_pool_public_metadata",
            safe_details=details,
        ),
        operational=OperationalEventFact(
            event_id=event_id,
            source=OperationalEventSource.PUBLIC_METADATA_TASK,
            operation_id=task_id,
            outcome=(
                OperationalEventOutcome.SUCCEEDED
                if event_type == OperationalEventType.PUBLIC_METADATA_TASK_COMPLETED
                else OperationalEventOutcome.FAILED
                if event_type == OperationalEventType.PUBLIC_METADATA_TASK_FAILED
                else OperationalEventOutcome.INTERRUPTED
            ),
        ),
    )


def _details(
    *,
    event_type: OperationalEventType,
    task_id: UUID,
    parser_run_id: UUID,
    provider_id: str,
    attempt_count: int,
    failure_code: str | None,
    next_attempt_at: AwareDatetime | None,
) -> PublicMetadataOperationalDetails:
    if event_type == OperationalEventType.PUBLIC_METADATA_TASK_COMPLETED:
        if failure_code is not None or next_attempt_at is not None:
            raise ValueError("completed public metadata tasks cannot contain failure details")
        return PublicMetadataTaskCompletedDetails(
            task_id=task_id,
            parser_run_id=parser_run_id,
            provider_id=provider_id,
            attempt_count=attempt_count,
        )
    if event_type == OperationalEventType.PUBLIC_METADATA_TASK_RETRY_SCHEDULED:
        if failure_code is None or next_attempt_at is None:
            raise ValueError("public metadata retries require a failure code and next attempt time")
        return PublicMetadataTaskRetryScheduledDetails(
            task_id=task_id,
            parser_run_id=parser_run_id,
            provider_id=provider_id,
            attempt_count=attempt_count,
            next_attempt_at=next_attempt_at,
            failure_code=failure_code,
        )
    if event_type == OperationalEventType.PUBLIC_METADATA_TASK_FAILED and failure_code is not None:
        return PublicMetadataTaskFailedDetails(
            task_id=task_id,
            parser_run_id=parser_run_id,
            provider_id=provider_id,
            attempt_count=attempt_count,
            failure_code=failure_code,
        )
    raise ValueError("failed public metadata tasks require only a failure code")
