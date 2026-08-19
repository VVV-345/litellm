"""校验脱敏快照差异，并将其原子转换为顶层字段人工覆盖事件。"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final, Literal, Protocol
from uuid import UUID, uuid5

from pydantic import AwareDatetime, JsonValue, TypeAdapter, ValidationError

from account_pool.auth.actor import ActorAction, ActorContext
from account_pool.parsing.imports.models import (
    SnapshotImportFailure,
    SnapshotImportFailureCode,
    SnapshotImportRequest,
    SnapshotImportResult,
    SnapshotImportSuccess,
)
from account_pool.parsing.models import ParsedChannelData
from account_pool.parsing.overrides.commands import OverrideEventResult
from account_pool.parsing.overrides.composer import OverrideApplyFailure, compose_effective_result
from account_pool.parsing.overrides.models import FieldOverrideEvent, OverrideAction, RootField, RootFieldTarget
from account_pool.parsing.overrides.repository import (
    OverrideEventBatchRepository,
    OverrideEventRepository,
    OverridePersistenceFailure,
    OverridePersistenceFailureCode,
)
from account_pool.parsing.persistence import ParserPersistenceFailure, ParserPersistenceFailureCode
from account_pool.parsing.repository import ParserRunRepository
from account_pool.parsing.safety import has_safe_parser_content

Clock = Callable[[], AwareDatetime]
_JSON_VALUE: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)
_ROOT_FIELDS: Final = tuple(RootField)


class SnapshotImporter(Protocol):
    async def import_snapshot(
        self,
        channel_id: UUID,
        request: SnapshotImportRequest,
        actor: ActorContext,
    ) -> SnapshotImportResult: ...


def utc_now() -> AwareDatetime:
    return datetime.now(UTC)


class SnapshotImportService:
    def __init__(
        self,
        parser_runs: ParserRunRepository,
        overrides: OverrideEventRepository,
        batch_writer: OverrideEventBatchRepository,
        clock: Clock = utc_now,
    ) -> None:
        self._parser_runs: Final = parser_runs
        self._overrides: Final = overrides
        self._batch_writer: Final = batch_writer
        self._clock: Final = clock

    async def import_snapshot(
        self,
        channel_id: UUID,
        request: SnapshotImportRequest,
        actor: ActorContext,
    ) -> SnapshotImportResult:
        if actor.action != ActorAction.SNAPSHOT_IMPORT or tuple(request.document) != (channel_id,):
            return _failure(SnapshotImportFailureCode.INVALID_REQUEST)
        imported: Final = request.document[channel_id]
        if not has_safe_parser_content(imported.model_dump_json()) or not has_safe_parser_content(request.reason):
            return _failure(SnapshotImportFailureCode.INVALID_DATA)
        loaded_runs: Final = await self._parser_runs.load_for_channel(channel_id=channel_id, limit=1)
        if isinstance(loaded_runs, ParserPersistenceFailure):
            return _from_parser_failure(loaded_runs)
        if not loaded_runs.records:
            return _failure(SnapshotImportFailureCode.RUN_NOT_FOUND)
        record: Final = loaded_runs.records[0]
        if not has_safe_parser_content(record.run.model_dump_json()):
            return _failure(SnapshotImportFailureCode.INVALID_DATA)
        loaded_events: Final = await self._overrides.load_for_channel(channel_id)
        if isinstance(loaded_events, OverridePersistenceFailure):
            return _from_override_failure(loaded_events)
        current: Final = compose_effective_result(record.run, loaded_events.events)
        if not has_safe_parser_content(current.model_dump_json()):
            return _failure(SnapshotImportFailureCode.INVALID_DATA)
        try:
            events: Final = _build_events(
                import_id=request.import_id,
                channel_id=channel_id,
                source_parser_run_id=record.run.parser_run_id,
                desired=imported.effective_result,
                current=current.effective_result,
                existing_events=loaded_events.events,
                actor=actor,
                reason=request.reason,
                occurred_at=self._clock(),
            )
        except (ValidationError, ValueError):
            return _failure(SnapshotImportFailureCode.INVALID_DATA)
        if not events:
            return _success(
                status="unchanged",
                import_id=request.import_id,
                channel_id=channel_id,
                source_parser_run_id=record.run.parser_run_id,
                events=(),
                effective_result=current.effective_result,
                applied_override_ids=current.applied_override_ids,
                override_failures=current.failures,
            )
        candidate: Final = compose_effective_result(record.run, (*loaded_events.events, *events))
        generated_ids: Final = frozenset(event.override_id for event in events)
        generated_failures: Final = tuple(
            failure for failure in candidate.failures if failure.override_id in generated_ids
        )
        if generated_failures or not generated_ids.issubset(candidate.applied_override_ids):
            return _failure(SnapshotImportFailureCode.INVALID_DATA)
        written: Final = await self._batch_writer.append_batch(events)
        if isinstance(written, OverridePersistenceFailure):
            return _from_override_failure(written)
        return _success(
            status=written.status,
            import_id=request.import_id,
            channel_id=channel_id,
            source_parser_run_id=record.run.parser_run_id,
            events=written.events,
            effective_result=candidate.effective_result,
            applied_override_ids=candidate.applied_override_ids,
            override_failures=candidate.failures,
        )


def _build_events(
    *,
    import_id: UUID,
    channel_id: UUID,
    source_parser_run_id: UUID,
    desired: ParsedChannelData,
    current: ParsedChannelData,
    existing_events: tuple[FieldOverrideEvent, ...],
    actor: ActorContext,
    reason: str,
    occurred_at: AwareDatetime,
) -> tuple[FieldOverrideEvent, ...]:
    desired_data: Final = _parsed_data(desired)
    current_data: Final = _parsed_data(current)
    return tuple(
        _event_for_field(
            import_id=import_id,
            channel_id=channel_id,
            source_parser_run_id=source_parser_run_id,
            field=field,
            value=_JSON_VALUE.validate_python(desired_data[field]),
            existing_events=existing_events,
            actor=actor,
            reason=reason,
            occurred_at=occurred_at,
        )
        for field in _ROOT_FIELDS
        if desired_data[field] != current_data[field]
    )


def _parsed_data(value: ParsedChannelData) -> dict[RootField, object]:
    dumped: Final = value.model_dump(mode="json")
    return {field: dumped[field.value] for field in _ROOT_FIELDS}


def _event_for_field(
    *,
    import_id: UUID,
    channel_id: UUID,
    source_parser_run_id: UUID,
    field: RootField,
    value: JsonValue,
    existing_events: tuple[FieldOverrideEvent, ...],
    actor: ActorContext,
    reason: str,
    occurred_at: AwareDatetime,
) -> FieldOverrideEvent:
    field_path: Final = f"/{field}"
    head: Final = _chain_head(existing_events, field_path)
    active: Final = head is not None and head.action == OverrideAction.SET
    return FieldOverrideEvent(
        override_id=uuid5(import_id, field_path),
        channel_id=channel_id,
        source_parser_run_id=source_parser_run_id,
        target=RootFieldTarget(field=field),
        action=OverrideAction.SET,
        value=value,
        had_previous_override=active,
        previous_value=head.value if active and head is not None else None,
        supersedes_override_id=None if head is None else head.override_id,
        actor_id=actor.user_id,
        actor_role=actor.role,
        request_id=actor.request_id,
        reason=reason,
        occurred_at=occurred_at,
    )


def _chain_head(events: tuple[FieldOverrideEvent, ...], field_path: str) -> FieldOverrideEvent | None:
    matching: Final = tuple(event for event in events if event.field_path() == field_path)
    superseded: Final = frozenset(
        event.supersedes_override_id for event in matching if event.supersedes_override_id is not None
    )
    heads: Final = tuple(event for event in matching if event.override_id not in superseded)
    if len(heads) > 1:
        raise ValueError("override field contains multiple chain heads")
    return None if not heads else heads[0]


def _success(
    *,
    status: Literal["created", "unchanged"],
    import_id: UUID,
    channel_id: UUID,
    source_parser_run_id: UUID,
    events: tuple[FieldOverrideEvent, ...],
    effective_result: ParsedChannelData,
    applied_override_ids: tuple[UUID, ...],
    override_failures: tuple[OverrideApplyFailure, ...],
) -> SnapshotImportSuccess:
    return SnapshotImportSuccess(
        status=status,
        import_id=import_id,
        channel_id=channel_id,
        source_parser_run_id=source_parser_run_id,
        events=tuple(
            OverrideEventResult(
                override_id=event.override_id,
                field_path=event.field_path(),
                action=event.action,
                source_parser_run_id=event.source_parser_run_id,
                actor_id=event.actor_id,
                actor_role=event.actor_role,
                request_id=event.request_id,
                occurred_at=event.occurred_at,
            )
            for event in events
        ),
        effective_result=effective_result,
        applied_override_ids=applied_override_ids,
        override_failures=override_failures,
    )


def _failure(code: SnapshotImportFailureCode, retryable: bool = False) -> SnapshotImportFailure:
    return SnapshotImportFailure(code=code, retryable=retryable)


def _from_parser_failure(failure: ParserPersistenceFailure) -> SnapshotImportFailure:
    if failure.code == ParserPersistenceFailureCode.CHANNEL_NOT_FOUND:
        return _failure(SnapshotImportFailureCode.CHANNEL_NOT_FOUND)
    if failure.code == ParserPersistenceFailureCode.DATABASE_UNAVAILABLE:
        return _failure(SnapshotImportFailureCode.DATABASE_UNAVAILABLE, retryable=True)
    if failure.code == ParserPersistenceFailureCode.RUN_NOT_FOUND:
        return _failure(SnapshotImportFailureCode.RUN_NOT_FOUND)
    return _failure(SnapshotImportFailureCode.INVALID_DATA)


def _from_override_failure(failure: OverridePersistenceFailure) -> SnapshotImportFailure:
    if failure.code == OverridePersistenceFailureCode.CHANNEL_NOT_FOUND:
        return _failure(SnapshotImportFailureCode.CHANNEL_NOT_FOUND)
    if failure.code == OverridePersistenceFailureCode.PREDECESSOR_CONFLICT:
        return _failure(SnapshotImportFailureCode.PREDECESSOR_CONFLICT)
    if failure.code == OverridePersistenceFailureCode.CONTENT_CONFLICT:
        return _failure(SnapshotImportFailureCode.CONTENT_CONFLICT)
    if failure.code == OverridePersistenceFailureCode.DATABASE_UNAVAILABLE:
        return _failure(SnapshotImportFailureCode.DATABASE_UNAVAILABLE, retryable=True)
    return _failure(SnapshotImportFailureCode.INVALID_DATA)
