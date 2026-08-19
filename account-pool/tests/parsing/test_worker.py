"""验证解析 Worker 的提交顺序、导出重试和人工兜底行为。"""

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from account_pool.domain.provider_source import ModelOffer, ProviderValidationResult
from account_pool.parsing.models import ParsedChannelData, ParserRun, ParserRunStatus
from account_pool.parsing.overrides.models import (
    FieldOverrideEvent,
    OverrideAction,
    RootField,
    RootFieldTarget,
    SubscriptionField,
    SubscriptionFieldTarget,
)
from account_pool.parsing.overrides.repository import (
    OverrideEventsLoadResult,
    OverrideEventsLoadSuccess,
    OverridePersistenceFailure,
    OverridePersistenceFailureCode,
    OverrideWriteResult,
)
from account_pool.parsing.persistence import (
    ParserExportAttempt,
    ParserExportStatus,
    ParserExportUpdateResult,
    ParserExportUpdateSuccess,
    ParserPersistenceFailure,
    ParserPersistenceFailureCode,
    ParserRunsLoadResult,
    ParserRunsLoadSuccess,
    ParserRunWriteResult,
    ParserRunWriteSuccess,
    PersistedParserRun,
)
from account_pool.parsing.registry import ParserSelectionRequest
from account_pool.parsing.snapshots import (
    SnapshotExportFailure,
    SnapshotExportFailureCode,
    SnapshotExportResult,
    SnapshotExportSuccess,
    project_parser_snapshot,
)
from account_pool.parsing.worker import (
    ParserWorker,
    ParserWorkFailure,
    ParserWorkRequest,
    ParserWorkSuccess,
)
from account_pool.provider_services.parser_registry import build_parser_registry
from pydantic import JsonValue

_CHANNEL_ID: Final = UUID("10000000-0000-0000-0000-000000000001")
_RUN_ID: Final = UUID("20000000-0000-0000-0000-000000000002")
_PARSED_AT: Final = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
_EXPORTED_AT: Final = datetime(2026, 8, 19, 12, 1, tzinfo=UTC)


class FakeParserRepository:
    def __init__(self, events: list[str]) -> None:
        self.events: Final = events
        self.record: PersistedParserRun | None = None
        self.persist_failure: ParserPersistenceFailure | None = None
        self.update_failure: ParserPersistenceFailure | None = None

    async def persist(self, run: ParserRun) -> ParserRunWriteResult:
        self.events.append("persist_committed")
        if self.persist_failure is not None:
            return self.persist_failure
        if self.record is None:
            self.record = PersistedParserRun(run=run, content_hash="a" * 64)
            return ParserRunWriteSuccess(status="created", record=self.record)
        return ParserRunWriteSuccess(status="unchanged", record=self.record)

    async def load_exportable(self, limit: int) -> ParserRunsLoadResult:
        self.events.append(f"load:{limit}")
        records: Final = () if self.record is None else (self.record,)
        return ParserRunsLoadSuccess(records=records)

    async def record_export_attempt(
        self,
        parser_run_id: UUID,
        attempt: ParserExportAttempt,
    ) -> ParserExportUpdateResult:
        self.events.append("record_export")
        if self.update_failure is not None:
            return self.update_failure
        assert self.record is not None
        assert self.record.run.parser_run_id == parser_run_id
        state: Final = attempt.next_state(self.record.export.attempt_count)
        self.record = self.record.model_copy(update={"export": state})
        return ParserExportUpdateSuccess(parser_run_id=parser_run_id, export=state)


class FakeOverrideRepository:
    def __init__(self, events: list[str]) -> None:
        self._events: Final = events
        self.overrides: tuple[FieldOverrideEvent, ...] = ()
        self.load_failure: OverridePersistenceFailure | None = None

    async def append(self, event: FieldOverrideEvent) -> OverrideWriteResult:
        raise AssertionError(f"worker must not append override events: {event.override_id}")

    async def load_for_channel(self, channel_id: UUID) -> OverrideEventsLoadResult:
        assert channel_id == _CHANNEL_ID
        self._events.append("load_overrides")
        if self.load_failure is not None:
            return self.load_failure
        return OverrideEventsLoadSuccess(events=self.overrides)


class RecordingSnapshotExporter:
    def __init__(self, events: list[str], failure: SnapshotExportFailure | None = None) -> None:
        self.events: Final = events
        self.failure = failure
        self.effective_results: list[ParsedChannelData | None] = []

    def export(
        self,
        run: ParserRun,
        effective_result: ParsedChannelData | None = None,
    ) -> SnapshotExportResult:
        self.events.append("snapshot_export")
        self.effective_results.append(effective_result)
        if self.failure is not None:
            return self.failure
        return SnapshotExportSuccess(snapshot=project_parser_snapshot(run, effective_result))


def _request(openai_compatible: bool = True) -> ParserWorkRequest:
    return ParserWorkRequest(
        channel_id=_CHANNEL_ID,
        parser_run_id=_RUN_ID,
        parsed_at=_PARSED_AT,
        selection=ParserSelectionRequest(
            provider_id="custom",
            api_base="https://gateway.example.com/v1",
            openai_compatible=openai_compatible,
        ),
        validation=ProviderValidationResult(
            ok=True,
            provider_id="openai_compatible",
            normalized_api_base="https://gateway.example.com/v1",
            group=None,
            key_fingerprint="fingerprint-must-not-persist",
            message="ok",
            capabilities=(),
            models=(ModelOffer(model="model-a"),),
        ),
    )


def _clock() -> datetime:
    return _EXPORTED_AT


def _override_event(
    override_id: UUID,
    target: RootFieldTarget | SubscriptionFieldTarget,
    value: JsonValue | None,
    *,
    action: OverrideAction = OverrideAction.SET,
    supersedes_override_id: UUID | None = None,
    previous_value: JsonValue | None = None,
    reason: str = "人工核对结果",
) -> FieldOverrideEvent:
    return FieldOverrideEvent(
        override_id=override_id,
        channel_id=_CHANNEL_ID,
        source_parser_run_id=_RUN_ID,
        target=target,
        action=action,
        value=value,
        had_previous_override=supersedes_override_id is not None,
        previous_value=previous_value,
        supersedes_override_id=supersedes_override_id,
        actor_id="admin-user",
        reason=reason,
        occurred_at=_EXPORTED_AT,
    )


async def test_worker_commits_before_snapshot_and_records_success() -> None:
    events: Final[list[str]] = []
    repository: Final = FakeParserRepository(events)
    overrides: Final = FakeOverrideRepository(events)
    exporter: Final = RecordingSnapshotExporter(events)
    worker: Final = ParserWorker(build_parser_registry(), repository, overrides, exporter, clock=_clock)

    result: Final = await worker.run(_request())

    assert isinstance(result, ParserWorkSuccess)
    assert result.status == "exported"
    assert result.persistence_status == "created"
    assert result.export.status == ParserExportStatus.SUCCEEDED
    assert events == ["persist_committed", "load_overrides", "snapshot_export", "record_export"]
    assert "fingerprint-must-not-persist" not in result.run.model_dump_json()


async def test_retryable_snapshot_failure_is_loaded_and_retried() -> None:
    events: Final[list[str]] = []
    repository: Final = FakeParserRepository(events)
    overrides: Final = FakeOverrideRepository(events)
    exporter: Final = RecordingSnapshotExporter(
        events,
        failure=SnapshotExportFailure(
            code=SnapshotExportFailureCode.LATEST_WRITE_FAILED,
            retryable=True,
        ),
    )
    worker: Final = ParserWorker(build_parser_registry(), repository, overrides, exporter, clock=_clock)

    first: Final = await worker.run(_request())
    assert isinstance(first, ParserWorkSuccess)
    assert first.status == "retry_scheduled"
    assert first.export.attempt_count == 1

    exporter.failure = None
    retried: Final = await worker.retry_exports(limit=10)

    assert retried.status == "processed"
    assert len(retried.outcomes) == 1
    outcome: Final = retried.outcomes[0]
    assert isinstance(outcome, ParserWorkSuccess)
    assert outcome.status == "exported"
    assert outcome.export.attempt_count == 2
    assert events == [
        "persist_committed",
        "load_overrides",
        "snapshot_export",
        "record_export",
        "load:10",
        "load_overrides",
        "snapshot_export",
        "record_export",
    ]


async def test_persistence_failure_prevents_snapshot_export() -> None:
    events: Final[list[str]] = []
    repository: Final = FakeParserRepository(events)
    repository.persist_failure = ParserPersistenceFailure(
        code=ParserPersistenceFailureCode.DATABASE_UNAVAILABLE,
        retryable=True,
    )
    worker: Final = ParserWorker(
        build_parser_registry(),
        repository,
        FakeOverrideRepository(events),
        RecordingSnapshotExporter(events),
        clock=_clock,
    )

    result: Final = await worker.run(_request())

    assert isinstance(result, ParserWorkFailure)
    assert result.stage == "persistence"
    assert events == ["persist_committed"]


async def test_export_state_write_failure_leaves_run_retryable() -> None:
    events: Final[list[str]] = []
    repository: Final = FakeParserRepository(events)
    repository.update_failure = ParserPersistenceFailure(
        code=ParserPersistenceFailureCode.DATABASE_UNAVAILABLE,
        retryable=True,
    )
    worker: Final = ParserWorker(
        build_parser_registry(),
        repository,
        FakeOverrideRepository(events),
        RecordingSnapshotExporter(events),
        clock=_clock,
    )

    first: Final = await worker.run(_request())
    assert isinstance(first, ParserWorkFailure)
    assert first.stage == "export_state"
    assert repository.record is not None
    assert repository.record.export.status == ParserExportStatus.PENDING

    repository.update_failure = None
    retried: Final = await worker.retry_exports()

    assert retried.status == "processed"
    assert len(retried.outcomes) == 1
    assert isinstance(retried.outcomes[0], ParserWorkSuccess)
    assert retried.outcomes[0].status == "exported"


async def test_unmatched_provider_persists_manual_required_run() -> None:
    events: Final[list[str]] = []
    repository: Final = FakeParserRepository(events)
    worker: Final = ParserWorker(
        build_parser_registry(),
        repository,
        FakeOverrideRepository(events),
        RecordingSnapshotExporter(events),
        clock=_clock,
    )

    result: Final = await worker.run(_request(openai_compatible=False))

    assert isinstance(result, ParserWorkSuccess)
    assert result.run.status == ParserRunStatus.MANUAL_REQUIRED
    assert result.run.parser_id == "manual"
    assert result.run.issues[0].stage == "parser_selection"


async def test_already_exported_run_skips_snapshot_rewrite() -> None:
    events: Final[list[str]] = []
    repository: Final = FakeParserRepository(events)
    first_worker: Final = ParserWorker(
        build_parser_registry(),
        repository,
        FakeOverrideRepository(events),
        RecordingSnapshotExporter(events),
        clock=_clock,
    )
    assert isinstance(await first_worker.run(_request()), ParserWorkSuccess)
    events.clear()

    second: Final = await first_worker.run(_request())

    assert isinstance(second, ParserWorkSuccess)
    assert second.status == "already_exported"
    assert events == ["persist_committed"]


async def test_reparse_keeps_active_override_in_effective_snapshot() -> None:
    override_id: Final = UUID("30000000-0000-0000-0000-000000000003")
    event: Final = _override_event(
        override_id,
        RootFieldTarget(field=RootField.WARNINGS),
        ["管理员已确认"],
    )
    first_events: Final[list[str]] = []
    first_overrides: Final = FakeOverrideRepository(first_events)
    first_overrides.overrides = (event,)
    first_exporter: Final = RecordingSnapshotExporter(first_events)
    first_worker: Final = ParserWorker(
        build_parser_registry(),
        FakeParserRepository(first_events),
        first_overrides,
        first_exporter,
        clock=_clock,
    )
    second_events: Final[list[str]] = []
    second_overrides: Final = FakeOverrideRepository(second_events)
    second_overrides.overrides = (event,)
    second_exporter: Final = RecordingSnapshotExporter(second_events)
    second_worker: Final = ParserWorker(
        build_parser_registry(),
        FakeParserRepository(second_events),
        second_overrides,
        second_exporter,
        clock=_clock,
    )

    first: Final = await first_worker.run(_request())
    reparsed_request: Final = _request().model_copy(
        update={"parser_run_id": UUID("20000000-0000-0000-0000-000000000004")}
    )
    reparsed: Final = await second_worker.run(reparsed_request)

    assert isinstance(first, ParserWorkSuccess)
    assert isinstance(reparsed, ParserWorkSuccess)
    assert first.applied_override_ids == (override_id,)
    assert reparsed.applied_override_ids == (override_id,)
    assert first_exporter.effective_results[0] is not None
    assert second_exporter.effective_results[0] is not None
    assert first_exporter.effective_results[0].warnings == ("管理员已确认",)
    assert second_exporter.effective_results[0].warnings == ("管理员已确认",)
    assert reparsed.run.result.warnings != ("管理员已确认",)


async def test_revoke_restores_raw_value_in_effective_snapshot() -> None:
    events: Final[list[str]] = []
    set_event: Final = _override_event(
        UUID("30000000-0000-0000-0000-000000000003"),
        RootFieldTarget(field=RootField.WARNINGS),
        ["管理员已确认"],
    )
    revoke_event: Final = _override_event(
        UUID("30000000-0000-0000-0000-000000000004"),
        RootFieldTarget(field=RootField.WARNINGS),
        None,
        action=OverrideAction.REVOKE,
        supersedes_override_id=set_event.override_id,
        previous_value=["管理员已确认"],
    )
    overrides: Final = FakeOverrideRepository(events)
    overrides.overrides = (set_event, revoke_event)
    exporter: Final = RecordingSnapshotExporter(events)
    worker: Final = ParserWorker(
        build_parser_registry(),
        FakeParserRepository(events),
        overrides,
        exporter,
        clock=_clock,
    )

    result: Final = await worker.run(_request())

    assert isinstance(result, ParserWorkSuccess)
    assert result.applied_override_ids == ()
    assert exporter.effective_results == [result.run.result]


async def test_override_load_failure_keeps_committed_run_pending() -> None:
    events: Final[list[str]] = []
    repository: Final = FakeParserRepository(events)
    overrides: Final = FakeOverrideRepository(events)
    overrides.load_failure = OverridePersistenceFailure(
        code=OverridePersistenceFailureCode.DATABASE_UNAVAILABLE,
        retryable=True,
    )
    worker: Final = ParserWorker(
        build_parser_registry(),
        repository,
        overrides,
        RecordingSnapshotExporter(events),
        clock=_clock,
    )

    result: Final = await worker.run(_request())

    assert isinstance(result, ParserWorkFailure)
    assert result.stage == "overrides"
    assert events == ["persist_committed", "load_overrides"]
    assert repository.record is not None
    assert repository.record.export.status == ParserExportStatus.PENDING


async def test_invalid_override_is_visible_without_exposing_event_content() -> None:
    events: Final[list[str]] = []
    marker: Final = "do-not-expose-marker"
    invalid: Final = _override_event(
        UUID("30000000-0000-0000-0000-000000000003"),
        SubscriptionFieldTarget(field=SubscriptionField.BALANCE),
        "42",
        reason=marker,
    )
    overrides: Final = FakeOverrideRepository(events)
    overrides.overrides = (invalid,)
    worker: Final = ParserWorker(
        build_parser_registry(),
        FakeParserRepository(events),
        overrides,
        RecordingSnapshotExporter(events),
        clock=_clock,
    )

    result: Final = await worker.run(_request())

    assert isinstance(result, ParserWorkSuccess)
    assert len(result.override_failures) == 1
    assert result.override_failures[0].override_id == invalid.override_id
    assert marker not in result.model_dump_json()
