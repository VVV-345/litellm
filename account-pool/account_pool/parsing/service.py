"""查询渠道解析历史，并将最新原始结果与人工覆盖合成为脱敏预览。"""

from enum import StrEnum
from typing import Final, Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, Field

from account_pool.models import FrozenModel
from account_pool.parsing.models import ParsedChannelData, ParserIssue, ParserRunStatus
from account_pool.parsing.overrides.composer import (
    OverrideApplyFailure,
    OverrideComposition,
    active_override_events,
    compose_effective_result,
)
from account_pool.parsing.overrides.models import FieldOverrideEvent
from account_pool.parsing.overrides.repository import (
    OverrideEventRepository,
    OverridePersistenceFailure,
    OverridePersistenceFailureCode,
)
from account_pool.parsing.persistence import (
    ParserExportState,
    ParserPersistenceFailure,
    ParserPersistenceFailureCode,
    PersistedParserRun,
)
from account_pool.parsing.repository import ParserRunRepository
from account_pool.parsing.safety import has_safe_parser_content
from account_pool.parsing.snapshots import ParserSnapshot, project_parser_snapshot


class ParserDataFailureCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    CHANNEL_NOT_FOUND = "channel_not_found"
    RUN_NOT_FOUND = "run_not_found"
    INVALID_DATA = "invalid_data"
    DATABASE_UNAVAILABLE = "database_unavailable"


class ParserDataFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: ParserDataFailureCode
    retryable: bool


class ParserRunSummary(FrozenModel):
    parser_run_id: UUID
    parser_id: str
    parser_version: str
    parsed_at: AwareDatetime
    status: ParserRunStatus
    discovered_models: tuple[str, ...] = ()
    issues: tuple[ParserIssue, ...] = ()
    export: ParserExportState


class ParserRunHistory(FrozenModel):
    status: Literal["loaded"] = "loaded"
    channel_id: UUID
    runs: tuple[ParserRunSummary, ...] = ()


class ActiveOverrideSummary(FrozenModel):
    override_id: UUID
    field_path: str = Field(min_length=1)
    source_parser_run_id: UUID
    occurred_at: AwareDatetime


class EffectiveParserData(FrozenModel):
    status: Literal["loaded"] = "loaded"
    channel_id: UUID
    parser_run_id: UUID
    parser_id: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    parsed_at: AwareDatetime
    parser_status: ParserRunStatus
    raw_result: ParsedChannelData
    effective_result: ParsedChannelData
    active_overrides: tuple[ActiveOverrideSummary, ...] = ()
    applied_override_ids: tuple[UUID, ...] = ()
    override_failures: tuple[OverrideApplyFailure, ...] = ()


ParserRunHistoryResult = ParserRunHistory | ParserDataFailure
EffectiveParserDataResult = EffectiveParserData | ParserDataFailure
ParserSnapshotResult = ParserSnapshot | ParserDataFailure


class _LatestProjection(FrozenModel):
    record: PersistedParserRun
    active_overrides: tuple[FieldOverrideEvent, ...]
    composition: OverrideComposition


class ParserDataReader(Protocol):
    async def history(self, channel_id: UUID, limit: int) -> ParserRunHistoryResult: ...

    async def effective_data(self, channel_id: UUID) -> EffectiveParserDataResult: ...

    async def snapshot(self, channel_id: UUID) -> ParserSnapshotResult: ...


class ParserDataService:
    def __init__(
        self,
        parser_runs: ParserRunRepository,
        overrides: OverrideEventRepository,
    ) -> None:
        self._parser_runs: Final = parser_runs
        self._overrides: Final = overrides

    async def history(self, channel_id: UUID, limit: int) -> ParserRunHistoryResult:
        loaded: Final = await self._parser_runs.load_for_channel(channel_id=channel_id, limit=limit)
        if isinstance(loaded, ParserPersistenceFailure):
            return _from_parser_failure(loaded)
        if any(not _is_safe_record(record) for record in loaded.records):
            return ParserDataFailure(code=ParserDataFailureCode.INVALID_DATA, retryable=False)
        return ParserRunHistory(
            channel_id=channel_id,
            runs=tuple(_summary(record) for record in loaded.records),
        )

    async def effective_data(self, channel_id: UUID) -> EffectiveParserDataResult:
        projection: Final = await self._latest_projection(channel_id)
        if isinstance(projection, ParserDataFailure):
            return projection
        record: Final = projection.record
        composition: Final = projection.composition
        return EffectiveParserData(
            channel_id=channel_id,
            parser_run_id=record.run.parser_run_id,
            parser_id=record.run.parser_id,
            parser_version=record.run.parser_version,
            parsed_at=record.run.parsed_at,
            parser_status=record.run.status,
            raw_result=composition.raw_result,
            effective_result=composition.effective_result,
            active_overrides=tuple(
                ActiveOverrideSummary(
                    override_id=event.override_id,
                    field_path=event.field_path(),
                    source_parser_run_id=event.source_parser_run_id,
                    occurred_at=event.occurred_at,
                )
                for event in projection.active_overrides
            ),
            applied_override_ids=composition.applied_override_ids,
            override_failures=composition.failures,
        )

    async def snapshot(self, channel_id: UUID) -> ParserSnapshotResult:
        projection: Final = await self._latest_projection(channel_id)
        if isinstance(projection, ParserDataFailure):
            return projection
        snapshot: Final = project_parser_snapshot(
            run=projection.record.run,
            effective_result=projection.composition.effective_result,
        )
        if not has_safe_parser_content(snapshot.model_dump_json()):
            return ParserDataFailure(code=ParserDataFailureCode.INVALID_DATA, retryable=False)
        return snapshot

    async def _latest_projection(self, channel_id: UUID) -> _LatestProjection | ParserDataFailure:
        loaded: Final = await self._parser_runs.load_for_channel(channel_id=channel_id, limit=1)
        if isinstance(loaded, ParserPersistenceFailure):
            return _from_parser_failure(loaded)
        if not loaded.records:
            return ParserDataFailure(code=ParserDataFailureCode.RUN_NOT_FOUND, retryable=False)
        record: Final = loaded.records[0]
        if not _is_safe_record(record):
            return ParserDataFailure(code=ParserDataFailureCode.INVALID_DATA, retryable=False)
        loaded_overrides: Final = await self._overrides.load_for_channel(channel_id)
        if isinstance(loaded_overrides, OverridePersistenceFailure):
            return _from_override_failure(loaded_overrides)
        composition: Final = compose_effective_result(record.run, loaded_overrides.events)
        active: Final = active_override_events(loaded_overrides.events)
        if not has_safe_parser_content(composition.model_dump_json()):
            return ParserDataFailure(code=ParserDataFailureCode.INVALID_DATA, retryable=False)
        return _LatestProjection(record=record, active_overrides=active, composition=composition)


def _summary(record: PersistedParserRun) -> ParserRunSummary:
    return ParserRunSummary(
        parser_run_id=record.run.parser_run_id,
        parser_id=record.run.parser_id,
        parser_version=record.run.parser_version,
        parsed_at=record.run.parsed_at,
        status=record.run.status,
        discovered_models=record.run.discovered_models,
        issues=record.run.issues,
        export=record.export,
    )


def _is_safe_record(record: PersistedParserRun) -> bool:
    return has_safe_parser_content(record.run.model_dump_json())


def _from_parser_failure(failure: ParserPersistenceFailure) -> ParserDataFailure:
    if failure.code == ParserPersistenceFailureCode.INVALID_REQUEST:
        return ParserDataFailure(code=ParserDataFailureCode.INVALID_REQUEST, retryable=False)
    if failure.code == ParserPersistenceFailureCode.CHANNEL_NOT_FOUND:
        return ParserDataFailure(code=ParserDataFailureCode.CHANNEL_NOT_FOUND, retryable=False)
    if failure.code == ParserPersistenceFailureCode.RUN_NOT_FOUND:
        return ParserDataFailure(code=ParserDataFailureCode.RUN_NOT_FOUND, retryable=False)
    if failure.code == ParserPersistenceFailureCode.DATABASE_UNAVAILABLE:
        return ParserDataFailure(code=ParserDataFailureCode.DATABASE_UNAVAILABLE, retryable=True)
    return ParserDataFailure(code=ParserDataFailureCode.INVALID_DATA, retryable=False)


def _from_override_failure(failure: OverridePersistenceFailure) -> ParserDataFailure:
    if failure.code == OverridePersistenceFailureCode.CHANNEL_NOT_FOUND:
        return ParserDataFailure(code=ParserDataFailureCode.CHANNEL_NOT_FOUND, retryable=False)
    if failure.code == OverridePersistenceFailureCode.DATABASE_UNAVAILABLE:
        return ParserDataFailure(code=ParserDataFailureCode.DATABASE_UNAVAILABLE, retryable=True)
    return ParserDataFailure(code=ParserDataFailureCode.INVALID_DATA, retryable=False)
