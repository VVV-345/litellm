"""验证快照导入只生成原子人工覆盖，并拒绝跨渠道、敏感内容和错误 actor。"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID

from account_pool.audit.models import ManagementAuditRecord, ManagementEventType
from account_pool.audit.repository import AuditLoadResult, AuditWriteResult, AuditWriteSuccess
from account_pool.auth.actor import ActorAction, ActorContext
from account_pool.parsing.imports.models import (
    SnapshotImportFailure,
    SnapshotImportFailureCode,
    SnapshotImportRequest,
    SnapshotImportSuccess,
)
from account_pool.parsing.imports.service import SnapshotImportService
from account_pool.parsing.models import ParsedChannelData, ParserRun, ParserRunStatus, SubscriptionData
from account_pool.parsing.overrides.models import FieldOverrideEvent, RootField
from account_pool.parsing.overrides.repository import (
    OverrideBatchWriteResult,
    OverrideBatchWriteSuccess,
    OverrideEventsLoadResult,
    OverrideEventsLoadSuccess,
    OverrideWriteResult,
)
from account_pool.parsing.persistence import (
    ParserExportAttempt,
    ParserExportUpdateResult,
    ParserRunsLoadResult,
    ParserRunsLoadSuccess,
    ParserRunWriteResult,
    PersistedParserRun,
)
from account_pool.parsing.snapshots import ParserSnapshot, project_parser_snapshot

_CHANNEL_ID: Final = UUID("10000000-0000-0000-0000-000000000001")
_RUN_ID: Final = UUID("20000000-0000-0000-0000-000000000002")
_IMPORT_ID: Final = UUID("30000000-0000-0000-0000-000000000003")
_NOW: Final = datetime(2026, 8, 20, 0, 0, tzinfo=UTC)


class FakeParserRunRepository:
    def __init__(self, record: PersistedParserRun) -> None:
        self._record: Final = record

    async def persist(self, run: ParserRun) -> ParserRunWriteResult:
        raise AssertionError(f"snapshot import cannot persist raw run: {run.parser_run_id}")

    async def load_exportable(self, limit: int) -> ParserRunsLoadResult:
        raise AssertionError(f"snapshot import cannot load export queue: {limit}")

    async def load_for_channel(self, channel_id: UUID, limit: int) -> ParserRunsLoadResult:
        assert channel_id == _CHANNEL_ID
        assert limit == 1
        return ParserRunsLoadSuccess(records=(self._record,))

    async def record_export_attempt(
        self,
        parser_run_id: UUID,
        attempt: ParserExportAttempt,
    ) -> ParserExportUpdateResult:
        raise AssertionError(f"snapshot import cannot change export state: {parser_run_id}, {attempt}")


class FakeOverrideRepository:
    def __init__(self) -> None:
        self.events: tuple[FieldOverrideEvent, ...] = ()
        self.batch_count = 0

    async def append(self, event: FieldOverrideEvent) -> OverrideWriteResult:
        raise AssertionError(f"snapshot import must use atomic batch append: {event.override_id}")

    async def append_batch(self, events: tuple[FieldOverrideEvent, ...]) -> OverrideBatchWriteResult:
        self.batch_count += 1
        self.events = (*self.events, *events)
        return OverrideBatchWriteSuccess(status="created", events=events)

    async def load_for_channel(self, channel_id: UUID) -> OverrideEventsLoadResult:
        assert channel_id == _CHANNEL_ID
        return OverrideEventsLoadSuccess(events=self.events)


class FakeAuditRepository:
    def __init__(self) -> None:
        self.records: tuple[ManagementAuditRecord, ...] = ()

    async def append(self, record: ManagementAuditRecord) -> AuditWriteResult:
        self.records = (*self.records, record)
        return AuditWriteSuccess(status="created", record=record)

    async def load(self, event_id: UUID) -> AuditLoadResult:
        raise AssertionError(f"snapshot importer must not load audit events: {event_id}")


def _run() -> ParserRun:
    return ParserRun(
        parser_run_id=_RUN_ID,
        channel_id=_CHANNEL_ID,
        parser_id="fixture-parser",
        parser_version="1.0.0",
        parsed_at=_NOW,
        status=ParserRunStatus.SUCCESS,
        result=ParsedChannelData(
            subscription=SubscriptionData(balance=Decimal("10")),
            warnings=("自动解析值",),
        ),
    )


def _record() -> PersistedParserRun:
    return PersistedParserRun(run=_run(), content_hash="a" * 64)


def _actor(action: ActorAction = ActorAction.SNAPSHOT_IMPORT) -> ActorContext:
    return ActorContext(
        user_id="admin-user",
        role="proxy_admin",
        request_id="request-import",
        action=action,
        envelope_id=UUID("40000000-0000-0000-0000-000000000004"),
    )


def _request(snapshot: ParserSnapshot) -> SnapshotImportRequest:
    return SnapshotImportRequest(
        import_id=_IMPORT_ID,
        reason="导入管理员核对后的脱敏快照",
        document={_CHANNEL_ID: snapshot},
    )


async def test_import_converts_differences_to_one_atomic_override_batch() -> None:
    raw: Final = _run()
    desired: Final = raw.result.model_copy(
        update={
            "subscription": SubscriptionData(balance=Decimal("25")),
            "warnings": ("管理员修正值",),
        }
    )
    repository: Final = FakeOverrideRepository()
    audit: Final = FakeAuditRepository()
    service: Final = SnapshotImportService(
        parser_runs=FakeParserRunRepository(_record()),
        overrides=repository,
        batch_writer=repository,
        audit=audit,
        clock=lambda: _NOW,
    )

    result: Final = await service.import_snapshot(
        _CHANNEL_ID,
        _request(project_parser_snapshot(raw, desired)),
        _actor(),
    )

    assert isinstance(result, SnapshotImportSuccess)
    assert result.status == "created"
    assert repository.batch_count == 1
    assert tuple(event.field_path() for event in repository.events) == (
        f"/{RootField.SUBSCRIPTION}",
        f"/{RootField.WARNINGS}",
    )
    assert all(event.actor_id == "admin-user" for event in repository.events)
    assert result.effective_result.subscription is not None
    assert result.effective_result.subscription.balance == Decimal("25")
    original_subscription: Final = _record().run.result.subscription
    assert original_subscription is not None
    assert original_subscription.balance == Decimal("10")
    assert len(audit.records) == 1
    assert audit.records[0].event.event_type == ManagementEventType.PARSER_SNAPSHOT_IMPORT
    assert audit.records[0].event.safe_details.changed_field_count == 2
    assert "管理员修正值" not in audit.records[0].model_dump_json()

    repeated: Final = await service.import_snapshot(
        _CHANNEL_ID,
        _request(project_parser_snapshot(raw, desired)),
        _actor(),
    )
    assert isinstance(repeated, SnapshotImportSuccess)
    assert repeated.status == "unchanged"
    assert repository.batch_count == 1


async def test_import_rejects_sensitive_cross_channel_and_wrong_actor_without_writes() -> None:
    raw: Final = _run()
    unsafe: Final = raw.result.model_copy(update={"warnings": ("authorization: bearer hidden",)})
    repository: Final = FakeOverrideRepository()
    service: Final = SnapshotImportService(
        parser_runs=FakeParserRunRepository(_record()),
        overrides=repository,
        batch_writer=repository,
        audit=FakeAuditRepository(),
        clock=lambda: _NOW,
    )
    other_channel: Final = UUID("50000000-0000-0000-0000-000000000005")
    mismatched: Final = SnapshotImportRequest(
        import_id=_IMPORT_ID,
        reason="错误渠道",
        document={other_channel: project_parser_snapshot(raw)},
    )

    sensitive_result: Final = await service.import_snapshot(
        _CHANNEL_ID,
        _request(project_parser_snapshot(raw, unsafe)),
        _actor(),
    )
    cross_channel: Final = await service.import_snapshot(_CHANNEL_ID, mismatched, _actor())
    wrong_actor: Final = await service.import_snapshot(
        _CHANNEL_ID,
        _request(project_parser_snapshot(raw)),
        _actor(ActorAction.OVERRIDE_SET),
    )

    assert isinstance(sensitive_result, SnapshotImportFailure)
    assert sensitive_result.code == SnapshotImportFailureCode.INVALID_DATA
    assert isinstance(cross_channel, SnapshotImportFailure)
    assert cross_channel.code == SnapshotImportFailureCode.INVALID_REQUEST
    assert isinstance(wrong_actor, SnapshotImportFailure)
    assert wrong_actor.code == SnapshotImportFailureCode.INVALID_REQUEST
    assert repository.events == ()
    assert repository.batch_count == 0
