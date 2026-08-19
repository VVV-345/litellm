"""验证覆盖应用服务的设置、修改、撤销、幂等和并发前置条件。"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID

from account_pool.auth.actor import ActorAction, ActorContext
from account_pool.parsing.models import ParsedChannelData, ParserRun, ParserRunStatus, SubscriptionData
from account_pool.parsing.overrides.commands import (
    OverrideMutationFailure,
    OverrideMutationFailureCode,
    OverrideMutationSuccess,
    OverrideRevokeRequest,
    OverrideSetRequest,
)
from account_pool.parsing.overrides.models import (
    FieldOverrideEvent,
    SubscriptionField,
    SubscriptionFieldTarget,
    SubscriptionModelTarget,
)
from account_pool.parsing.overrides.repository import (
    OverrideEventsLoadResult,
    OverrideEventsLoadSuccess,
    OverridePersistenceFailure,
    OverridePersistenceFailureCode,
    OverrideWriteResult,
    OverrideWriteSuccess,
)
from account_pool.parsing.overrides.service import ParserOverrideService
from account_pool.parsing.persistence import (
    ParserExportAttempt,
    ParserExportUpdateResult,
    ParserRunsLoadResult,
    ParserRunsLoadSuccess,
    ParserRunWriteResult,
    PersistedParserRun,
)

_CHANNEL_ID: Final = UUID("10000000-0000-0000-0000-000000000001")
_RUN_ID: Final = UUID("20000000-0000-0000-0000-000000000002")
_FIRST_OVERRIDE_ID: Final = UUID("30000000-0000-0000-0000-000000000003")
_SECOND_OVERRIDE_ID: Final = UUID("30000000-0000-0000-0000-000000000004")
_NOW: Final = datetime(2026, 8, 19, 21, 0, tzinfo=UTC)


class FakeParserRunRepository:
    def __init__(self) -> None:
        run: Final = ParserRun(
            parser_run_id=_RUN_ID,
            channel_id=_CHANNEL_ID,
            parser_id="fixture-parser",
            parser_version="1.0.0",
            parsed_at=_NOW,
            status=ParserRunStatus.SUCCESS,
            result=ParsedChannelData(subscription=SubscriptionData(balance=Decimal("10"))),
        )
        self._record: Final = PersistedParserRun(run=run, content_hash="a" * 64)

    async def persist(self, run: ParserRun) -> ParserRunWriteResult:
        raise AssertionError(f"override service must not persist parser runs: {run.parser_run_id}")

    async def load_exportable(self, limit: int) -> ParserRunsLoadResult:
        raise AssertionError(f"override service must not load export queue: {limit}")

    async def load_for_channel(self, channel_id: UUID, limit: int) -> ParserRunsLoadResult:
        assert channel_id == _CHANNEL_ID
        assert limit == 1
        return ParserRunsLoadSuccess(records=(self._record,))

    async def record_export_attempt(
        self,
        parser_run_id: UUID,
        attempt: ParserExportAttempt,
    ) -> ParserExportUpdateResult:
        raise AssertionError(f"override service must not update export: {parser_run_id}, {attempt.attempted_at}")


class FakeOverrideRepository:
    def __init__(self) -> None:
        self.events: tuple[FieldOverrideEvent, ...] = ()
        self.append_count = 0

    async def append(self, event: FieldOverrideEvent) -> OverrideWriteResult:
        self.append_count += 1
        existing: Final = next(
            (candidate for candidate in self.events if candidate.override_id == event.override_id),
            None,
        )
        if existing is not None:
            if existing == event:
                return OverrideWriteSuccess(status="unchanged", event=existing)
            return OverridePersistenceFailure(
                code=OverridePersistenceFailureCode.CONTENT_CONFLICT,
                retryable=False,
            )
        self.events = (*self.events, event)
        return OverrideWriteSuccess(status="created", event=event)

    async def load_for_channel(self, channel_id: UUID) -> OverrideEventsLoadResult:
        assert channel_id == _CHANNEL_ID
        return OverrideEventsLoadSuccess(events=self.events)


def _clock() -> datetime:
    return _NOW


def _actor(action: ActorAction) -> ActorContext:
    return ActorContext(
        user_id="admin-user",
        role="proxy_admin",
        request_id="request-123",
        action=action,
        envelope_id=UUID("40000000-0000-0000-0000-000000000004"),
    )


def _set_request(
    override_id: UUID = _FIRST_OVERRIDE_ID,
    value: str = "20",
    expected_override_id: UUID | None = None,
    reason: str = "人工核对余额",
) -> OverrideSetRequest:
    return OverrideSetRequest(
        override_id=override_id,
        target=SubscriptionFieldTarget(field=SubscriptionField.BALANCE),
        value=value,
        expected_override_id=expected_override_id,
        reason=reason,
    )


async def test_set_modify_and_revoke_preserve_raw_result_and_actor() -> None:
    overrides: Final = FakeOverrideRepository()
    service: Final = ParserOverrideService(FakeParserRunRepository(), overrides, clock=_clock)

    first: Final = await service.set_override(
        _CHANNEL_ID,
        _set_request(),
        _actor(ActorAction.OVERRIDE_SET),
    )
    modified: Final = await service.set_override(
        _CHANNEL_ID,
        _set_request(
            override_id=_SECOND_OVERRIDE_ID,
            value="30",
            expected_override_id=_FIRST_OVERRIDE_ID,
            reason="再次核对余额",
        ),
        _actor(ActorAction.OVERRIDE_SET),
    )
    revoked: Final = await service.revoke_override(
        _CHANNEL_ID,
        "/subscription/balance",
        OverrideRevokeRequest(
            override_id=UUID("30000000-0000-0000-0000-000000000005"),
            expected_override_id=_SECOND_OVERRIDE_ID,
            reason="恢复自动解析",
        ),
        _actor(ActorAction.OVERRIDE_REVOKE),
    )

    assert isinstance(first, OverrideMutationSuccess)
    assert first.effective_result.subscription is not None
    assert first.effective_result.subscription.balance == Decimal("20")
    assert isinstance(modified, OverrideMutationSuccess)
    assert modified.effective_result.subscription is not None
    assert modified.effective_result.subscription.balance == Decimal("30")
    assert isinstance(revoked, OverrideMutationSuccess)
    assert revoked.effective_result.subscription is not None
    assert revoked.effective_result.subscription.balance == Decimal("10")
    assert tuple(event.actor_id for event in overrides.events) == ("admin-user",) * 3
    assert tuple(event.actor_role for event in overrides.events) == ("proxy_admin",) * 3
    assert tuple(event.request_id for event in overrides.events) == ("request-123",) * 3
    assert first.event.actor_role == "proxy_admin"
    assert first.event.request_id == "request-123"
    assert overrides.events[1].previous_value == "20"
    assert overrides.events[2].previous_value == "30"


async def test_same_business_request_is_idempotent_without_new_event() -> None:
    overrides: Final = FakeOverrideRepository()
    service: Final = ParserOverrideService(FakeParserRunRepository(), overrides, clock=_clock)
    request: Final = _set_request()
    actor: Final = _actor(ActorAction.OVERRIDE_SET)

    created: Final = await service.set_override(_CHANNEL_ID, request, actor)
    repeated: Final = await service.set_override(_CHANNEL_ID, request, actor)

    assert isinstance(created, OverrideMutationSuccess)
    assert created.status == "created"
    assert isinstance(repeated, OverrideMutationSuccess)
    assert repeated.status == "unchanged"
    assert len(overrides.events) == 1
    assert overrides.append_count == 1


async def test_stale_predecessor_and_wrong_actor_action_are_rejected() -> None:
    overrides: Final = FakeOverrideRepository()
    service: Final = ParserOverrideService(FakeParserRunRepository(), overrides, clock=_clock)
    assert isinstance(
        await service.set_override(_CHANNEL_ID, _set_request(), _actor(ActorAction.OVERRIDE_SET)),
        OverrideMutationSuccess,
    )

    stale: Final = await service.set_override(
        _CHANNEL_ID,
        _set_request(override_id=_SECOND_OVERRIDE_ID, value="30"),
        _actor(ActorAction.OVERRIDE_SET),
    )
    wrong_action: Final = await service.set_override(
        _CHANNEL_ID,
        _set_request(override_id=UUID("30000000-0000-0000-0000-000000000006")),
        _actor(ActorAction.OVERRIDE_REVOKE),
    )

    assert isinstance(stale, OverrideMutationFailure)
    assert stale.code == OverrideMutationFailureCode.PREDECESSOR_CONFLICT
    assert isinstance(wrong_action, OverrideMutationFailure)
    assert wrong_action.code == OverrideMutationFailureCode.INVALID_REQUEST
    assert len(overrides.events) == 1


async def test_invalid_target_and_sensitive_reason_are_not_persisted() -> None:
    overrides: Final = FakeOverrideRepository()
    service: Final = ParserOverrideService(FakeParserRunRepository(), overrides, clock=_clock)
    missing_target: Final = OverrideSetRequest(
        override_id=_FIRST_OVERRIDE_ID,
        target=SubscriptionModelTarget(provider_model_id="missing-model"),
        value={"provider_model_id": "missing-model"},
        reason="人工核对模型",
    )

    missing: Final = await service.set_override(
        _CHANNEL_ID,
        missing_target,
        _actor(ActorAction.OVERRIDE_SET),
    )
    sensitive: Final = await service.set_override(
        _CHANNEL_ID,
        _set_request(
            override_id=_SECOND_OVERRIDE_ID,
            reason="see https://private.example",
        ),
        _actor(ActorAction.OVERRIDE_SET),
    )

    assert isinstance(missing, OverrideMutationFailure)
    assert missing.code == OverrideMutationFailureCode.INVALID_VALUE
    assert isinstance(sensitive, OverrideMutationFailure)
    assert sensitive.code == OverrideMutationFailureCode.INVALID_VALUE
    assert overrides.events == ()
