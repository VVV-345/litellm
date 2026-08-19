"""验证解析查询服务的历史摘要、有效数据合成和失败映射。"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID

from account_pool.parsing.models import ParsedChannelData, ParserRun, ParserRunStatus, SubscriptionData
from account_pool.parsing.overrides.models import (
    FieldOverrideEvent,
    OverrideAction,
    SubscriptionField,
    SubscriptionFieldTarget,
)
from account_pool.parsing.overrides.repository import (
    OverrideEventsLoadResult,
    OverrideEventsLoadSuccess,
    OverrideWriteResult,
)
from account_pool.parsing.persistence import (
    ParserExportAttempt,
    ParserExportUpdateResult,
    ParserPersistenceFailure,
    ParserPersistenceFailureCode,
    ParserRunsLoadResult,
    ParserRunsLoadSuccess,
    ParserRunWriteResult,
    PersistedParserRun,
)
from account_pool.parsing.service import (
    EffectiveParserData,
    ParserDataFailure,
    ParserDataFailureCode,
    ParserDataService,
    ParserRunHistory,
)

_CHANNEL_ID: Final = UUID("10000000-0000-0000-0000-000000000001")
_RUN_ID: Final = UUID("20000000-0000-0000-0000-000000000002")
_OVERRIDE_ID: Final = UUID("30000000-0000-0000-0000-000000000003")
_NOW: Final = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)


class FakeParserRunRepository:
    def __init__(
        self,
        records: tuple[PersistedParserRun, ...] = (),
        failure: ParserPersistenceFailure | None = None,
    ) -> None:
        self._records: Final = records
        self._failure: Final = failure

    async def persist(self, run: ParserRun) -> ParserRunWriteResult:
        raise AssertionError(f"query service must not persist parser run: {run.parser_run_id}")

    async def load_exportable(self, limit: int) -> ParserRunsLoadResult:
        raise AssertionError(f"query service must not load export queue: {limit}")

    async def load_for_channel(self, channel_id: UUID, limit: int) -> ParserRunsLoadResult:
        assert channel_id == _CHANNEL_ID
        assert 1 <= limit <= 100
        if self._failure is not None:
            return self._failure
        return ParserRunsLoadSuccess(records=self._records[:limit])

    async def record_export_attempt(
        self,
        parser_run_id: UUID,
        attempt: ParserExportAttempt,
    ) -> ParserExportUpdateResult:
        raise AssertionError(f"query service must not update export: {parser_run_id}, {attempt.attempted_at}")


class FakeOverrideRepository:
    def __init__(self, events: tuple[FieldOverrideEvent, ...] = ()) -> None:
        self._events: Final = events

    async def append(self, event: FieldOverrideEvent) -> OverrideWriteResult:
        raise AssertionError(f"query service must not append override: {event.override_id}")

    async def load_for_channel(self, channel_id: UUID) -> OverrideEventsLoadResult:
        assert channel_id == _CHANNEL_ID
        return OverrideEventsLoadSuccess(events=self._events)


def _record() -> PersistedParserRun:
    return PersistedParserRun(
        run=ParserRun(
            parser_run_id=_RUN_ID,
            channel_id=_CHANNEL_ID,
            parser_id="fixture-parser",
            parser_version="1.0.0",
            parsed_at=_NOW,
            status=ParserRunStatus.SUCCESS,
            result=ParsedChannelData(subscription=SubscriptionData(balance=Decimal("10"))),
            discovered_models=("model-a",),
        ),
        content_hash="a" * 64,
    )


def _override() -> FieldOverrideEvent:
    return FieldOverrideEvent(
        override_id=_OVERRIDE_ID,
        channel_id=_CHANNEL_ID,
        source_parser_run_id=_RUN_ID,
        target=SubscriptionFieldTarget(field=SubscriptionField.BALANCE),
        action=OverrideAction.SET,
        value="20",
        actor_id="admin-user",
        reason="人工核对余额",
        occurred_at=_NOW,
    )


async def test_history_returns_safe_run_summaries() -> None:
    service: Final = ParserDataService(FakeParserRunRepository((_record(),)), FakeOverrideRepository())

    result: Final = await service.history(_CHANNEL_ID, limit=25)

    assert isinstance(result, ParserRunHistory)
    assert len(result.runs) == 1
    assert result.runs[0].parser_run_id == _RUN_ID
    assert result.runs[0].discovered_models == ("model-a",)
    assert "content_hash" not in result.model_dump_json()
    assert "raw_result" not in result.model_dump_json()


async def test_effective_data_applies_active_override_without_changing_raw() -> None:
    service: Final = ParserDataService(
        FakeParserRunRepository((_record(),)),
        FakeOverrideRepository((_override(),)),
    )

    result: Final = await service.effective_data(_CHANNEL_ID)

    assert isinstance(result, EffectiveParserData)
    assert result.raw_result.subscription is not None
    assert result.raw_result.subscription.balance == Decimal("10")
    assert result.effective_result.subscription is not None
    assert result.effective_result.subscription.balance == Decimal("20")
    assert result.applied_override_ids == (_OVERRIDE_ID,)
    assert result.active_overrides[0].field_path == "/subscription/balance"
    assert "人工核对余额" not in result.model_dump_json()


async def test_query_failures_and_missing_run_are_explicit() -> None:
    unavailable: Final = ParserDataService(
        FakeParserRunRepository(
            failure=ParserPersistenceFailure(
                code=ParserPersistenceFailureCode.DATABASE_UNAVAILABLE,
                retryable=True,
            )
        ),
        FakeOverrideRepository(),
    )
    empty: Final = ParserDataService(FakeParserRunRepository(), FakeOverrideRepository())

    failed: Final = await unavailable.history(_CHANNEL_ID, limit=25)
    missing: Final = await empty.effective_data(_CHANNEL_ID)

    assert isinstance(failed, ParserDataFailure)
    assert failed.code == ParserDataFailureCode.DATABASE_UNAVAILABLE
    assert failed.retryable
    assert isinstance(missing, ParserDataFailure)
    assert missing.code == ParserDataFailureCode.RUN_NOT_FOUND


async def test_query_service_refuses_unsafe_repository_data() -> None:
    safe: Final = _record()
    unsafe: Final = safe.model_copy(
        update={
            "run": safe.run.model_copy(
                update={"result": safe.run.result.model_copy(update={"warnings": ("api_key=hidden",)})}
            )
        }
    )
    service: Final = ParserDataService(FakeParserRunRepository((unsafe,)), FakeOverrideRepository())

    history: Final = await service.history(_CHANNEL_ID, limit=25)
    effective: Final = await service.effective_data(_CHANNEL_ID)

    assert isinstance(history, ParserDataFailure)
    assert history.code == ParserDataFailureCode.INVALID_DATA
    assert isinstance(effective, ParserDataFailure)
    assert effective.code == ParserDataFailureCode.INVALID_DATA
    assert "hidden" not in history.model_dump_json()
