"""编排解析器选择、权威数据提交、JSON 导出和失败重试。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final, Literal, Protocol, assert_never
from uuid import UUID

from pydantic import AwareDatetime

from account_pool.domain.provider_source import ProviderValidationResult
from account_pool.models import FrozenModel
from account_pool.operational.models import (
    OperationalEventType,
    ParserSnapshotExportTrigger,
    build_parser_snapshot_export_record,
)
from account_pool.operational.repository import OperationalEventRepository
from account_pool.parsing.models import (
    ParsedChannelData,
    ParserFailureCategory,
    ParserIssue,
    ParserRun,
    ParserRunStatus,
)
from account_pool.parsing.overrides.composer import (
    OverrideApplyFailure,
    compose_effective_result,
)
from account_pool.parsing.overrides.repository import (
    OverrideEventRepository,
    OverridePersistenceFailure,
)
from account_pool.parsing.persistence import (
    ParserExportAttempt,
    ParserExportState,
    ParserExportStatus,
    ParserPersistenceFailure,
    PersistedParserRun,
)
from account_pool.parsing.registry import ParserRegistry, ParserSelection, ParserSelectionRequest
from account_pool.parsing.repository import ParserRunRepository
from account_pool.parsing.snapshots import (
    SnapshotExportFailure,
    SnapshotExportResult,
)

Clock = Callable[[], AwareDatetime]
ParserWorkStatus = Literal[
    "exported",
    "retry_scheduled",
    "export_failed_permanently",
    "already_exported",
]


class ParserSnapshotExporter(Protocol):
    def export(
        self,
        run: ParserRun,
        effective_result: ParsedChannelData | None = None,
    ) -> SnapshotExportResult: ...


class ParserWorkRequest(FrozenModel):
    channel_id: UUID
    parser_run_id: UUID
    parsed_at: AwareDatetime
    selection: ParserSelectionRequest
    validation: ProviderValidationResult


class ParserWorkSuccess(FrozenModel):
    status: ParserWorkStatus
    run: ParserRun
    persistence_status: Literal["created", "unchanged"]
    export: ParserExportState
    applied_override_ids: tuple[UUID, ...] = ()
    override_failures: tuple[OverrideApplyFailure, ...] = ()


class ParserWorkFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    stage: Literal["persistence", "overrides", "export_state"]
    run: ParserRun
    failure: ParserPersistenceFailure | OverridePersistenceFailure


ParserWorkResult = ParserWorkSuccess | ParserWorkFailure


class ParserRetryBatchSuccess(FrozenModel):
    status: Literal["processed"] = "processed"
    outcomes: tuple[ParserWorkResult, ...] = ()


class ParserRetryBatchFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    failure: ParserPersistenceFailure


ParserRetryBatchResult = ParserRetryBatchSuccess | ParserRetryBatchFailure


def utc_now() -> AwareDatetime:
    return datetime.now(UTC)


class ParserWorker:
    def __init__(
        self,
        registry: ParserRegistry,
        repository: ParserRunRepository,
        overrides: OverrideEventRepository,
        snapshots: ParserSnapshotExporter,
        operations: OperationalEventRepository | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self._registry: Final = registry
        self._repository: Final = repository
        self._overrides: Final = overrides
        self._snapshots: Final = snapshots
        self._operations: Final = operations
        self._clock: Final = clock

    async def run(self, request: ParserWorkRequest) -> ParserWorkResult:
        run: Final = self._parse(request)
        persisted: Final = await self._repository.persist(run)
        if isinstance(persisted, ParserPersistenceFailure):
            return ParserWorkFailure(stage="persistence", run=run, failure=persisted)
        return await self._export_persisted(
            persisted.record,
            persisted.status,
            ParserSnapshotExportTrigger.INITIAL,
        )

    async def retry_exports(self, limit: int = 25) -> ParserRetryBatchResult:
        loaded: Final = await self._repository.load_exportable(limit)
        if isinstance(loaded, ParserPersistenceFailure):
            return ParserRetryBatchFailure(failure=loaded)
        outcomes: Final = await self._export_records(loaded.records)
        return ParserRetryBatchSuccess(outcomes=outcomes)

    def _parse(self, request: ParserWorkRequest) -> ParserRun:
        selection: Final = self._registry.select(request.selection)
        if selection.parser_id is None:
            return _manual_run(request=request, selection=selection)
        parser: Final = self._registry.resolve(selection.parser_id)
        if parser is None:
            return _manual_run(request=request, selection=selection)
        return parser(
            request.channel_id,
            request.parser_run_id,
            request.parsed_at,
            request.validation,
        )

    async def _export_persisted(
        self,
        record: PersistedParserRun,
        persistence_status: Literal["created", "unchanged"],
        trigger: ParserSnapshotExportTrigger,
    ) -> ParserWorkResult:
        if record.export.status == ParserExportStatus.SUCCEEDED:
            return ParserWorkSuccess(
                status="already_exported",
                run=record.run,
                persistence_status=persistence_status,
                export=record.export,
            )
        if record.export.status == ParserExportStatus.PERMANENT_FAILURE:
            return ParserWorkSuccess(
                status="export_failed_permanently",
                run=record.run,
                persistence_status=persistence_status,
                export=record.export,
            )

        # persist() 返回前事务已经提交，后续覆盖读取或快照失败不会撤销原始解析结果。
        loaded_overrides: Final = await self._overrides.load_for_channel(record.run.channel_id)
        if isinstance(loaded_overrides, OverridePersistenceFailure):
            return ParserWorkFailure(stage="overrides", run=record.run, failure=loaded_overrides)
        composition: Final = compose_effective_result(record.run, loaded_overrides.events)
        snapshot_result: Final = self._snapshots.export(record.run, composition.effective_result)
        attempt: Final = _export_attempt(snapshot_result=snapshot_result, attempted_at=self._clock())
        updated: Final = await self._repository.record_export_attempt(record.run.parser_run_id, attempt)
        if isinstance(updated, ParserPersistenceFailure):
            return ParserWorkFailure(stage="export_state", run=record.run, failure=updated)
        await self._record_snapshot_export(record.run, updated.export, trigger)
        return ParserWorkSuccess(
            status=_work_status(updated.export),
            run=record.run,
            persistence_status=persistence_status,
            export=updated.export,
            applied_override_ids=composition.applied_override_ids,
            override_failures=composition.failures,
        )

    async def _export_records(
        self,
        records: tuple[PersistedParserRun, ...],
    ) -> tuple[ParserWorkResult, ...]:
        if not records:
            return ()
        first: Final = await self._export_persisted(
            records[0],
            "unchanged",
            ParserSnapshotExportTrigger.RETRY,
        )
        remaining: Final = await self._export_records(records[1:])
        return (first, *remaining)

    async def _record_snapshot_export(
        self,
        run: ParserRun,
        export: ParserExportState,
        trigger: ParserSnapshotExportTrigger,
    ) -> None:
        if self._operations is None:
            return
        assert export.last_attempt_at is not None
        await self._operations.append(
            build_parser_snapshot_export_record(
                channel_id=run.channel_id,
                parser_run_id=run.parser_run_id,
                occurred_at=export.last_attempt_at,
                event_type=_snapshot_event_type(export.status),
                attempt_count=export.attempt_count,
                trigger=trigger,
                failure_code=None if export.failure_code is None else export.failure_code.value,
            )
        )


def _manual_run(request: ParserWorkRequest, selection: ParserSelection) -> ParserRun:
    return ParserRun(
        parser_run_id=request.parser_run_id,
        channel_id=request.channel_id,
        parser_id="manual",
        parser_version="1.0.0",
        parsed_at=request.parsed_at,
        status=ParserRunStatus.MANUAL_REQUIRED,
        issues=(
            ParserIssue(
                parser_id="manual",
                parser_version="1.0.0",
                stage="parser_selection",
                category=ParserFailureCategory.MANUAL_REQUIRED,
                retryable=False,
                next_action="选择已注册解析器或通过人工覆盖补充渠道数据",
                evidence_summary=selection.reason,
                first_seen_at=request.parsed_at,
                latest_seen_at=request.parsed_at,
            ),
        ),
    )


def _export_attempt(
    snapshot_result: SnapshotExportResult,
    attempted_at: AwareDatetime,
) -> ParserExportAttempt:
    if isinstance(snapshot_result, SnapshotExportFailure):
        return ParserExportAttempt(
            attempted_at=attempted_at,
            failure_code=snapshot_result.code,
            failure_retryable=snapshot_result.retryable,
        )
    return ParserExportAttempt(attempted_at=attempted_at)


def _work_status(export: ParserExportState) -> ParserWorkStatus:
    if export.status == ParserExportStatus.SUCCEEDED:
        return "exported"
    if export.status == ParserExportStatus.RETRYABLE_FAILURE:
        return "retry_scheduled"
    return "export_failed_permanently"


def _snapshot_event_type(status: ParserExportStatus) -> OperationalEventType:
    match status:
        case ParserExportStatus.SUCCEEDED:
            return OperationalEventType.PARSER_SNAPSHOT_EXPORTED
        case ParserExportStatus.RETRYABLE_FAILURE:
            return OperationalEventType.PARSER_SNAPSHOT_EXPORT_RETRY_SCHEDULED
        case ParserExportStatus.PERMANENT_FAILURE:
            return OperationalEventType.PARSER_SNAPSHOT_EXPORT_FAILED
        case ParserExportStatus.PENDING:
            raise ValueError("pending snapshot exports do not have an attempt event")
    assert_never(status)
