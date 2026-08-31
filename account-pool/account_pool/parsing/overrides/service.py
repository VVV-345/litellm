"""校验人工覆盖命令，以追加事件方式设置或撤销字段并返回最新有效结果。"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final, Literal, Protocol
from uuid import UUID, uuid5

from pydantic import AwareDatetime, ValidationError

from account_pool.audit.models import (
    AuditOutcome,
    ParserOverrideRevokeDetails,
    ParserOverrideSetDetails,
    SafeAuditOutcome,
    build_management_audit_record,
)
from account_pool.audit.repository import AuditPersistenceFailure, ManagementAuditRepository
from account_pool.auth.actor import ActorAction, ActorContext
from account_pool.models import FrozenModel
from account_pool.parsing.overrides.commands import (
    OverrideEventResult,
    OverrideMutationFailure,
    OverrideMutationFailureCode,
    OverrideMutationResult,
    OverrideMutationSuccess,
    OverrideRevokeRequest,
    OverrideSetRequest,
)
from account_pool.parsing.overrides.composer import compose_effective_result
from account_pool.parsing.overrides.models import FieldOverrideEvent, OverrideAction
from account_pool.parsing.overrides.repository import (
    OverrideEventRepository,
    OverridePersistenceFailure,
    OverridePersistenceFailureCode,
)
from account_pool.parsing.persistence import (
    ParserPersistenceFailure,
    ParserPersistenceFailureCode,
    PersistedParserRun,
)
from account_pool.parsing.repository import ParserRunRepository
from account_pool.parsing.safety import has_safe_parser_content

Clock = Callable[[], AwareDatetime]
_OVERRIDE_AUDIT_NAMESPACE: Final = UUID("35ecce8e-cc72-4ac8-a1d8-7869fd5a9f2e")


class _LoadedState(FrozenModel):
    record: PersistedParserRun
    events: tuple[FieldOverrideEvent, ...]


class ParserOverrideWriter(Protocol):
    async def set_override(
        self,
        channel_id: UUID,
        request: OverrideSetRequest,
        actor: ActorContext,
    ) -> OverrideMutationResult: ...

    async def revoke_override(
        self,
        channel_id: UUID,
        field_path: str,
        request: OverrideRevokeRequest,
        actor: ActorContext,
    ) -> OverrideMutationResult: ...


class ParserRuntimeProjector(Protocol):
    async def project(self) -> object: ...


def utc_now() -> AwareDatetime:
    return datetime.now(UTC)


class ParserOverrideService:
    def __init__(
        self,
        parser_runs: ParserRunRepository,
        overrides: OverrideEventRepository,
        audit: ManagementAuditRepository,
        projector: ParserRuntimeProjector | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self._parser_runs: Final = parser_runs
        self._overrides: Final = overrides
        self._audit: Final = audit
        self._projector: Final = projector
        self._clock: Final = clock

    async def set_override(
        self,
        channel_id: UUID,
        request: OverrideSetRequest,
        actor: ActorContext,
    ) -> OverrideMutationResult:
        if actor.action != ActorAction.OVERRIDE_SET:
            return _failure(OverrideMutationFailureCode.INVALID_REQUEST)
        result: Final = await self._set_override(channel_id, request, actor)
        projected: Final = await self._project(result)
        return await self._audited(
            channel_id=channel_id,
            actor=actor,
            override_id=request.override_id,
            result=projected,
        )

    async def _set_override(
        self,
        channel_id: UUID,
        request: OverrideSetRequest,
        actor: ActorContext,
    ) -> OverrideMutationResult:
        loaded: Final = await self._load(channel_id)
        if isinstance(loaded, OverrideMutationFailure):
            return loaded
        # override_id 是业务幂等键：相同命令重试返回原事件，不同内容复用同一 ID 则拒绝。
        existing: Final = _event_by_id(loaded.events, request.override_id)
        if isinstance(existing, OverrideMutationFailure):
            return existing
        if existing is not None:
            if not _matches_set_retry(existing=existing, request=request, actor=actor):
                return _failure(OverrideMutationFailureCode.CONTENT_CONFLICT)
            return _success(status="unchanged", state=loaded, event=existing)
        current: Final = _chain_head(loaded.events, request.target.field_path())
        if isinstance(current, OverrideMutationFailure):
            return current
        current_id: Final = None if current is None else current.override_id
        # expected_override_id 相当于乐观锁，防止两个管理员基于同一旧状态同时覆盖。
        if current_id != request.expected_override_id:
            return _failure(OverrideMutationFailureCode.PREDECESSOR_CONFLICT)
        current_is_active: Final = current is not None and current.action == OverrideAction.SET
        try:
            event: Final = FieldOverrideEvent(
                override_id=request.override_id,
                channel_id=channel_id,
                source_parser_run_id=loaded.record.run.parser_run_id,
                target=request.target,
                action=OverrideAction.SET,
                value=request.value,
                had_previous_override=current_is_active,
                previous_value=current.value if current_is_active and current is not None else None,
                supersedes_override_id=current_id,
                actor_id=actor.user_id,
                actor_role=actor.role,
                request_id=actor.request_id,
                reason=request.reason,
                occurred_at=self._clock(),
            )
        except ValidationError:
            return _failure(OverrideMutationFailureCode.INVALID_VALUE)
        candidate_events: Final = (*loaded.events, event)
        # 写库前先合成完整结果，避免单字段合法但破坏跨字段约束的事件进入审计链。
        composition: Final = compose_effective_result(loaded.record.run, candidate_events)
        candidate_failure: Final = next(
            (failure for failure in composition.failures if failure.override_id == event.override_id),
            None,
        )
        if candidate_failure is not None:
            return OverrideMutationFailure(
                code=OverrideMutationFailureCode.INVALID_VALUE,
                retryable=False,
                apply_failure_code=candidate_failure.code,
            )
        if event.override_id not in composition.applied_override_ids:
            return _failure(OverrideMutationFailureCode.INVALID_DATA)
        written: Final = await self._overrides.append(event)
        if isinstance(written, OverridePersistenceFailure):
            return _from_override_failure(written)
        state: Final = _LoadedState(record=loaded.record, events=candidate_events)
        return _success(status=written.status, state=state, event=written.event)

    async def revoke_override(
        self,
        channel_id: UUID,
        field_path: str,
        request: OverrideRevokeRequest,
        actor: ActorContext,
    ) -> OverrideMutationResult:
        if actor.action != ActorAction.OVERRIDE_REVOKE or not field_path.startswith("/"):
            return _failure(OverrideMutationFailureCode.INVALID_REQUEST)
        result: Final = await self._revoke_override(channel_id, field_path, request, actor)
        projected: Final = await self._project(result)
        return await self._audited(
            channel_id=channel_id,
            actor=actor,
            override_id=request.override_id,
            result=projected,
        )

    async def _revoke_override(
        self,
        channel_id: UUID,
        field_path: str,
        request: OverrideRevokeRequest,
        actor: ActorContext,
    ) -> OverrideMutationResult:
        loaded: Final = await self._load(channel_id)
        if isinstance(loaded, OverrideMutationFailure):
            return loaded
        existing: Final = _event_by_id(loaded.events, request.override_id)
        if isinstance(existing, OverrideMutationFailure):
            return existing
        if existing is not None:
            if not _matches_revoke_retry(
                existing=existing,
                field_path=field_path,
                request=request,
                actor=actor,
            ):
                return _failure(OverrideMutationFailureCode.CONTENT_CONFLICT)
            return _success(status="unchanged", state=loaded, event=existing)
        current: Final = _chain_head(loaded.events, field_path)
        if isinstance(current, OverrideMutationFailure):
            return current
        if current is None or current.action != OverrideAction.SET:
            return _failure(OverrideMutationFailureCode.OVERRIDE_NOT_FOUND)
        if current.override_id != request.expected_override_id:
            return _failure(OverrideMutationFailureCode.PREDECESSOR_CONFLICT)
        try:
            event: Final = FieldOverrideEvent(
                override_id=request.override_id,
                channel_id=channel_id,
                source_parser_run_id=loaded.record.run.parser_run_id,
                target=current.target,
                action=OverrideAction.REVOKE,
                value=None,
                had_previous_override=True,
                previous_value=current.value,
                supersedes_override_id=current.override_id,
                actor_id=actor.user_id,
                actor_role=actor.role,
                request_id=actor.request_id,
                reason=request.reason,
                occurred_at=self._clock(),
            )
        except ValidationError:
            return _failure(OverrideMutationFailureCode.INVALID_VALUE)
        candidate_events: Final = (*loaded.events, event)
        written: Final = await self._overrides.append(event)
        if isinstance(written, OverridePersistenceFailure):
            return _from_override_failure(written)
        state: Final = _LoadedState(record=loaded.record, events=candidate_events)
        return _success(status=written.status, state=state, event=written.event)

    async def _audited(
        self,
        *,
        channel_id: UUID,
        actor: ActorContext,
        override_id: UUID,
        result: OverrideMutationResult,
    ) -> OverrideMutationResult:
        failure_code: Final = result.code.value if isinstance(result, OverrideMutationFailure) else None
        outcome: Final = SafeAuditOutcome(
            status=AuditOutcome.FAILED if failure_code is not None else AuditOutcome.SUCCEEDED,
            failure_code=failure_code,
        )
        safe_field_path: Final = None if isinstance(result, OverrideMutationFailure) else result.event.field_path
        occurred_at: Final = self._clock() if isinstance(result, OverrideMutationFailure) else result.event.occurred_at
        details: Final = (
            ParserOverrideSetDetails(
                outcome=outcome,
                override_id=override_id,
                field_path=safe_field_path,
            )
            if actor.action == ActorAction.OVERRIDE_SET
            else ParserOverrideRevokeDetails(
                outcome=outcome,
                override_id=override_id,
                field_path=safe_field_path,
            )
        )
        audit_result: Final = await self._audit.append(
            build_management_audit_record(
                event_id=uuid5(
                    _OVERRIDE_AUDIT_NAMESPACE,
                    f"{actor.envelope_id}:{actor.action}:{override_id}:{outcome.status}:{failure_code}",
                ),
                occurred_at=occurred_at,
                actor=actor,
                channel_id=channel_id,
                details=details,
            )
        )
        if isinstance(audit_result, AuditPersistenceFailure):
            return OverrideMutationFailure(
                code=OverrideMutationFailureCode.AUDIT_UNAVAILABLE,
                retryable=audit_result.retryable,
            )
        return result

    async def _load(self, channel_id: UUID) -> _LoadedState | OverrideMutationFailure:
        loaded_runs: Final = await self._parser_runs.load_for_channel(channel_id, limit=1)
        if isinstance(loaded_runs, ParserPersistenceFailure):
            return _from_parser_failure(loaded_runs)
        if not loaded_runs.records:
            return _failure(OverrideMutationFailureCode.RUN_NOT_FOUND)
        record: Final = loaded_runs.records[0]
        if not has_safe_parser_content(record.run.model_dump_json()):
            return _failure(OverrideMutationFailureCode.INVALID_DATA)
        loaded_events: Final = await self._overrides.load_for_channel(channel_id)
        if isinstance(loaded_events, OverridePersistenceFailure):
            return _from_override_failure(loaded_events)
        return _LoadedState(record=record, events=loaded_events.events)

    async def _project(self, result: OverrideMutationResult) -> OverrideMutationResult:
        if isinstance(result, OverrideMutationFailure) or self._projector is None:
            return result
        try:
            await self._projector.project()
        except Exception:
            return OverrideMutationFailure(
                code=OverrideMutationFailureCode.RUNTIME_PROJECTION_FAILED,
                retryable=True,
            )
        return result


def _success(
    status: Literal["created", "unchanged"],
    state: _LoadedState,
    event: FieldOverrideEvent,
) -> OverrideMutationSuccess:
    composition: Final = compose_effective_result(state.record.run, state.events)
    return OverrideMutationSuccess(
        status=status,
        event=OverrideEventResult(
            override_id=event.override_id,
            field_path=event.field_path(),
            action=event.action,
            source_parser_run_id=event.source_parser_run_id,
            actor_id=event.actor_id,
            actor_role=event.actor_role,
            request_id=event.request_id,
            occurred_at=event.occurred_at,
        ),
        effective_result=composition.effective_result,
        applied_override_ids=composition.applied_override_ids,
        override_failures=composition.failures,
    )


def _event_by_id(
    events: tuple[FieldOverrideEvent, ...],
    override_id: UUID,
) -> FieldOverrideEvent | OverrideMutationFailure | None:
    matches: Final = tuple(event for event in events if event.override_id == override_id)
    if len(matches) > 1:
        return _failure(OverrideMutationFailureCode.INVALID_DATA)
    return None if not matches else matches[0]


def _chain_head(
    events: tuple[FieldOverrideEvent, ...],
    field_path: str,
) -> FieldOverrideEvent | OverrideMutationFailure | None:
    matching: Final = tuple(event for event in events if event.field_path() == field_path)
    superseded: Final = frozenset(
        event.supersedes_override_id for event in matching if event.supersedes_override_id is not None
    )
    heads: Final = tuple(event for event in matching if event.override_id not in superseded)
    if len(heads) > 1:
        return _failure(OverrideMutationFailureCode.INVALID_DATA)
    return None if not heads else heads[0]


def _matches_set_retry(
    existing: FieldOverrideEvent,
    request: OverrideSetRequest,
    actor: ActorContext,
) -> bool:
    return (
        existing.action == OverrideAction.SET
        and existing.target == request.target
        and existing.value == request.value
        and existing.supersedes_override_id == request.expected_override_id
        and existing.actor_id == actor.user_id
        and existing.reason == request.reason
    )


def _matches_revoke_retry(
    existing: FieldOverrideEvent,
    field_path: str,
    request: OverrideRevokeRequest,
    actor: ActorContext,
) -> bool:
    return (
        existing.action == OverrideAction.REVOKE
        and existing.field_path() == field_path
        and existing.supersedes_override_id == request.expected_override_id
        and existing.actor_id == actor.user_id
        and existing.reason == request.reason
    )


def _failure(code: OverrideMutationFailureCode) -> OverrideMutationFailure:
    return OverrideMutationFailure(code=code, retryable=False)


def _from_parser_failure(failure: ParserPersistenceFailure) -> OverrideMutationFailure:
    if failure.code == ParserPersistenceFailureCode.CHANNEL_NOT_FOUND:
        return _failure(OverrideMutationFailureCode.CHANNEL_NOT_FOUND)
    if failure.code == ParserPersistenceFailureCode.DATABASE_UNAVAILABLE:
        return OverrideMutationFailure(code=OverrideMutationFailureCode.DATABASE_UNAVAILABLE, retryable=True)
    if failure.code == ParserPersistenceFailureCode.INVALID_REQUEST:
        return _failure(OverrideMutationFailureCode.INVALID_REQUEST)
    return _failure(OverrideMutationFailureCode.INVALID_DATA)


def _from_override_failure(failure: OverridePersistenceFailure) -> OverrideMutationFailure:
    if failure.code == OverridePersistenceFailureCode.CHANNEL_NOT_FOUND:
        return _failure(OverrideMutationFailureCode.CHANNEL_NOT_FOUND)
    if failure.code == OverridePersistenceFailureCode.PREDECESSOR_CONFLICT:
        return _failure(OverrideMutationFailureCode.PREDECESSOR_CONFLICT)
    if failure.code == OverridePersistenceFailureCode.CONTENT_CONFLICT:
        return _failure(OverrideMutationFailureCode.CONTENT_CONFLICT)
    if failure.code == OverridePersistenceFailureCode.DATABASE_UNAVAILABLE:
        return OverrideMutationFailure(code=OverrideMutationFailureCode.DATABASE_UNAVAILABLE, retryable=True)
    return _failure(OverrideMutationFailureCode.INVALID_DATA)
