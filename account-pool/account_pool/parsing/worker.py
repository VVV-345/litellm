"""编排解析器选择、权威数据提交、JSON 导出和失败重试。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final, Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime

from account_pool.domain.provider_source import ProviderValidationResult
from account_pool.models import FrozenModel
from account_pool.parsing.models import (
    ParsedChannelData,
    ParserFailureCategory,
    ParserIssue,
    ParserRun,
    ParserRunStatus,
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


class ParserWorkFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    stage: Literal["persistence", "export_state"]
    run: ParserRun
    failure: ParserPersistenceFailure


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
        snapshots: ParserSnapshotExporter,
        clock: Clock = utc_now,
    ) -> None:
        self._registry: Final = registry
        self._repository: Final = repository
        self._snapshots: Final = snapshots
        self._clock: Final = clock

    async def run(self, request: ParserWorkRequest) -> ParserWorkResult:
        run: Final = self._parse(request)
        persisted: Final = await self._repository.persist(run)
        if isinstance(persisted, ParserPersistenceFailure):
            return ParserWorkFailure(stage="persistence", run=run, failure=persisted)
        return await self._export_persisted(persisted.record, persisted.status)

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

        # persist() 返回前事务已经提交，快照失败只会改变导出状态，不会撤销解析结果。
        snapshot_result: Final = self._snapshots.export(record.run)
        attempt: Final = _export_attempt(snapshot_result=snapshot_result, attempted_at=self._clock())
        updated: Final = await self._repository.record_export_attempt(record.run.parser_run_id, attempt)
        if isinstance(updated, ParserPersistenceFailure):
            return ParserWorkFailure(stage="export_state", run=record.run, failure=updated)
        return ParserWorkSuccess(
            status=_work_status(updated.export),
            run=record.run,
            persistence_status=persistence_status,
            export=updated.export,
        )

    async def _export_records(
        self,
        records: tuple[PersistedParserRun, ...],
    ) -> tuple[ParserWorkResult, ...]:
        if not records:
            return ()
        first: Final = await self._export_persisted(records[0], "unchanged")
        remaining: Final = await self._export_records(records[1:])
        return (first, *remaining)


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
