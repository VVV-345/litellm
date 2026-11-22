"""本文件验证删除环境与请求收尾会原子清理运行时租约、会话绑定和冷却状态。"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Final, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from account_pool.domain import ChannelKind, EnvironmentStatus, ModelCooldown, SupplierKind, utc_now
from account_pool.gateway_contracts import Lease
from account_pool.gateway_repository import _release_lease, reserve_token_budget, settle_model_cooldowns
from account_pool.repository import _delete_environment
from psycopg_pool import PoolTimeout
from test_account_pool import _record


@dataclass
class RuntimeState:
    environments: set[UUID]
    leases: dict[UUID, tuple[UUID, UUID]]
    sessions: dict[str, tuple[UUID | None, UUID]]
    cooldowns: set[UUID]
    settled_tokens: dict[tuple[UUID, int, datetime], int] = field(default_factory=dict)


class ResultCursor:
    def __init__(self, row: dict[str, object] | None = None) -> None:
        self.row = row

    async def fetchone(self) -> dict[str, object] | None:
        return self.row


class RuntimeConnection:
    def __init__(self, state: RuntimeState) -> None:
        self.state = state

    async def execute(self, query: str, params: tuple[object, ...] = ()) -> ResultCursor:
        statement = " ".join(query.split())
        if statement.startswith(
            ("SELECT id FROM account_pool_environments", "SELECT id, payload FROM account_pool_environments")
        ):
            environment_id = cast(UUID, params[0])
            return ResultCursor({"id": environment_id} if environment_id in self.state.environments else None)
        if statement.startswith("DELETE FROM account_pool_leases WHERE account_id"):
            environment_id = cast(UUID, params[0])
            card_id = UUID(cast(str, params[1]))
            self.state.leases = {
                lease_id: scope
                for lease_id, scope in self.state.leases.items()
                if scope[0] != environment_id and scope[1] != card_id
            }
            return ResultCursor()
        if statement.startswith("DELETE FROM account_pool_leases WHERE lease_id"):
            lease_id: Final = cast(UUID, params[0])
            existed: Final = self.state.leases.pop(lease_id, None)
            return ResultCursor({"lease_id": lease_id} if existed is not None else None)
        if statement.startswith("DELETE FROM account_pool_sessions"):
            environment_id = cast(UUID, params[0])
            self.state.sessions = {
                binding: scope
                for binding, scope in self.state.sessions.items()
                if scope[0] != environment_id and scope[1] != environment_id
            }
            return ResultCursor()
        if statement.startswith("DELETE FROM account_pool_runtime_cooldown"):
            self.state.cooldowns.discard(cast(UUID, params[0]))
            return ResultCursor()
        if statement.startswith("INSERT INTO account_pool_runtime_cooldown"):
            self.state.cooldowns.add(cast(UUID, params[0]))
            return ResultCursor()
        if statement.startswith("UPDATE account_pool_token_budget_windows"):
            tokens: Final = cast(int, params[0])
            budget_key: Final = (cast(UUID, params[1]), cast(int, params[2]), cast(datetime, params[3]))
            self.state.settled_tokens[budget_key] = self.state.settled_tokens.get(budget_key, 0) + tokens
            return ResultCursor()
        if statement.startswith("DELETE FROM account_pool_environments"):
            self.state.environments.discard(cast(UUID, params[0]))
            return ResultCursor()
        raise AssertionError(f"Unexpected query: {statement}")


@dataclass
class BudgetState:
    settled_tokens: dict[tuple[UUID, int, datetime], int]
    reservations: tuple[tuple[UUID, int, datetime, int], ...]


class BudgetConnection:
    def __init__(self, state: BudgetState) -> None:
        self.state = state

    async def execute(self, query: str, params: tuple[object, ...] = ()) -> ResultCursor:
        statement: Final = " ".join(query.split())
        if statement.startswith("INSERT INTO account_pool_token_budget_windows"):
            account_id: Final = cast(UUID, params[0])
            window_seconds: Final = cast(int, params[1])
            window_started_at: Final = cast(datetime, params[2])
            budget_key: Final = (account_id, window_seconds, window_started_at)
            settled_tokens: Final = self.state.settled_tokens.setdefault(budget_key, 0)
            return ResultCursor({"window_started_at": window_started_at, "settled_tokens": settled_tokens})
        if statement.startswith("SELECT COALESCE(sum((payload->>'reserved_tokens')::bigint)"):
            reservation_key: Final = (cast(UUID, params[0]), cast(int, params[1]), cast(datetime, params[2]))
            reserved_tokens: Final = sum(
                tokens
                for account_id, window_seconds, window_started_at, tokens in self.state.reservations
                if (account_id, window_seconds, window_started_at) == reservation_key
            )
            return ResultCursor({"reserved_tokens": reserved_tokens})
        raise AssertionError(f"Unexpected query: {statement}")


def connection(state: RuntimeState) -> psycopg.AsyncConnection[dict[str, object]]:
    return cast(psycopg.AsyncConnection[dict[str, object]], RuntimeConnection(state))


def budget_connection(state: BudgetState) -> psycopg.AsyncConnection[dict[str, object]]:
    return cast(psycopg.AsyncConnection[dict[str, object]], BudgetConnection(state))


def lease(card_id: UUID, account_id: UUID) -> Lease:
    return Lease(
        lease_id=uuid4(),
        card_id=card_id,
        key_id=uuid4(),
        account_id=account_id,
        request_id=uuid4(),
        channel=ChannelKind.CLIPROXYAPI,
        supplier=SupplierKind.OPENAI_CODEX,
        model="gpt-5",
        started_at=utc_now(),
    )


def test_model_cooldown_preserves_other_models_and_newer_concurrent_failures() -> None:
    now = utc_now()
    active = lease(uuid4(), uuid4()).model_copy(update={"started_at": now})
    other = ModelCooldown(model="other-model", retry_at=now + timedelta(minutes=2))
    failed = settle_model_cooldowns((other,), active, 60, False, now)
    assert len(failed) == 2 and failed[0] == other
    assert failed[1].model == active.model and failed[1].retry_at == now + timedelta(seconds=60)
    assert settle_model_cooldowns(failed, active, 0, True, now) == failed
    probe = active.model_copy(update={"started_at": now + timedelta(seconds=61)})
    assert settle_model_cooldowns(failed, probe, 0, True, now + timedelta(seconds=62)) == (other,)
    repeated = settle_model_cooldowns(failed, active, 1, False, now)
    assert repeated == failed


def test_repeated_model_timeouts_cool_across_minutes_and_ignore_stale_results() -> None:
    account_id: Final = uuid4()
    card_id: Final = uuid4()
    started: Final = utc_now()
    first_lease: Final = lease(card_id, account_id).model_copy(update={"started_at": started})
    first: Final = settle_model_cooldowns((), first_lease, 0, False, started, timeout_failure=True)
    assert first[0].consecutive_timeouts == 1
    assert first[0].retry_at == started
    assert first[0].reason == "timeout_tracking"

    second_started: Final = started + timedelta(minutes=30)
    second_lease: Final = lease(card_id, account_id).model_copy(update={"started_at": second_started})
    second: Final = settle_model_cooldowns(first, second_lease, 0, False, second_started, timeout_failure=True)
    assert second[0].consecutive_timeouts == 2
    assert second[0].retry_at == second_started
    assert second[0].reason == "timeout_tracking"

    third_started: Final = second_started + timedelta(days=1)
    third_lease: Final = lease(card_id, account_id).model_copy(update={"started_at": third_started})
    third: Final = settle_model_cooldowns(second, third_lease, 0, False, third_started, timeout_failure=True)
    assert third[0].consecutive_timeouts == 3
    assert third[0].retry_at == third_started + timedelta(minutes=5)
    assert third[0].reason == "consecutive_timeouts"

    stale_success: Final = lease(card_id, account_id).model_copy(
        update={"started_at": third_started - timedelta(seconds=1)}
    )
    assert settle_model_cooldowns(third, stale_success, 0, True, third_started + timedelta(minutes=6)) == third

    success_started: Final = third_started + timedelta(minutes=6)
    success_lease: Final = lease(card_id, account_id).model_copy(update={"started_at": success_started})
    reset: Final = settle_model_cooldowns(third, success_lease, 0, True, success_started, timeout_failure=False)
    assert reset[0].consecutive_timeouts == 0
    assert reset[0].retry_at == success_started
    assert reset[0].reason == "timeout_tracking"
    assert (
        settle_model_cooldowns(
            reset, third_lease, 0, False, success_started + timedelta(seconds=1), timeout_failure=True
        )
        == reset
    )


@pytest.mark.asyncio
async def test_timeout_failure_persists_streak_without_an_active_cooldown() -> None:
    request: Final = lease(uuid4(), uuid4())
    record: Final = _record(status=EnvironmentStatus.READY).model_copy(update={"id": request.account_id})

    class CooldownConnection:
        def __init__(self) -> None:
            self.statements: list[str] = []

        async def execute(self, query: str, params: tuple[object, ...] = ()) -> ResultCursor:
            statement: Final = " ".join(query.split())
            self.statements.append(statement)
            if statement.startswith("SELECT id, payload FROM account_pool_environments"):
                return ResultCursor({"id": request.account_id, "payload": record.model_dump(mode="json")})
            if statement.startswith("DELETE FROM account_pool_leases WHERE lease_id"):
                return ResultCursor({"lease_id": request.lease_id})
            return ResultCursor()

    connection: Final = CooldownConnection()
    await _release_lease(connection, request, 0, None, timeout_failure=True)
    assert any("SET payload = jsonb_set" in statement for statement in connection.statements)


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_cooldown", (False, True))
async def test_release_does_not_rewrite_unchanged_model_cooldowns(prior_cooldown: bool) -> None:
    request: Final = lease(uuid4(), uuid4())
    cooldowns: Final = (
        (ModelCooldown(model=request.model, retry_at=request.started_at + timedelta(minutes=1)),)
        if prior_cooldown else ()
    )
    record: Final = _record(status=EnvironmentStatus.READY).model_copy(
        update={"id": request.account_id, "model_cooldowns": cooldowns}
    )

    class UnchangedConnection:
        def __init__(self) -> None:
            self.statements: tuple[str, ...] = ()

        async def execute(self, query: str, params: tuple[object, ...] = ()) -> ResultCursor:
            self.statements = (*self.statements, query)
            if query.startswith("SELECT id, payload"):
                return ResultCursor({"id": request.account_id, "payload": record.model_dump(mode="json")})
            if query.startswith("DELETE FROM account_pool_leases"):
                return ResultCursor({"lease_id": request.lease_id})
            return ResultCursor()

    connection: Final = UnchangedConnection()
    await _release_lease(connection, request, 0, None, model_succeeded=True)
    assert any("FOR UPDATE" in statement for statement in connection.statements)
    assert not any("SET payload = jsonb_set" in statement for statement in connection.statements)


@pytest.mark.asyncio
async def test_database_pool_reuses_bounds_rolls_back_and_keeps_other_dsns_direct() -> None:
    from account_pool.repository import database_connection, database_pool

    url: Final = os.environ.get("ACCOUNT_POOL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires an isolated ACCOUNT_POOL_TEST_DATABASE_URL")
    ready: Final = asyncio.Event()

    async def sibling_request() -> int:
        await ready.wait()
        async with database_connection(url) as connection:
            result: Final = await connection.execute("SELECT pg_backend_pid() AS pid")
            row: Final = await result.fetchone()
            assert row is not None
            return int(row["pid"])

    sibling: Final = asyncio.create_task(sibling_request())
    async with database_pool(url, max_size=1, timeout=0.1) as pool:
        await pool.wait(timeout=3)
        async with database_connection(url) as original:
            result: Final = await original.execute("SELECT pg_backend_pid() AS pid")
            row: Final = await result.fetchone()
            assert row is not None
            pid: Final = row["pid"]
            await original.execute("CREATE TEMP TABLE pool_probe (value integer)")
            await original.execute("INSERT INTO pool_probe VALUES (1)")
            with pytest.raises(PoolTimeout):
                async with database_connection(url):
                    pytest.fail("pool exceeded its connection limit")
        with pytest.raises(RuntimeError, match="rollback probe"):
            async with database_connection(url) as failed:
                await failed.execute("INSERT INTO pool_probe VALUES (2)")
                raise RuntimeError("rollback probe")
        async with database_connection(url) as reused:
            values: Final = await reused.execute("SELECT sum(value) AS total FROM pool_probe")
            total: Final = await values.fetchone()
            assert total is not None and total["total"] == 1
            assert reused is original
        ready.set()
        assert await sibling == pid
        scoped: Final = psycopg.conninfo.make_conninfo(url, application_name="pool-unregistered-test")
        async with database_connection(scoped) as direct:
            other: Final = await direct.execute("SELECT pg_backend_pid() AS pid")
            other_row: Final = await other.fetchone()
            assert other_row is not None and other_row["pid"] != pid
        assert direct.closed
    assert pool.closed and original.closed
    async with database_connection(url) as after_close:
        await after_close.execute("SELECT 1")
    assert after_close.closed
    async with database_pool(url, max_size=1) as reopened:
        async with database_connection(url) as connection:
            await connection.execute("SELECT 1")
    assert reopened.closed


@pytest.mark.asyncio
async def test_database_pool_returns_connection_after_cancelled_transaction_and_closes_on_failure() -> None:
    from account_pool.repository import database_connection, database_pool

    url: Final = os.environ.get("ACCOUNT_POOL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires an isolated ACCOUNT_POOL_TEST_DATABASE_URL")
    entered: Final = asyncio.Event()

    async def cancelled_request() -> None:
        async with database_connection(url) as connection:
            entered.set()
            await connection.execute("SELECT pg_sleep(30)")

    with pytest.raises(RuntimeError, match="startup failure"):
        async with database_pool(url, max_size=1, timeout=1) as pool:
            task: Final = asyncio.create_task(cancelled_request())
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            async with database_connection(url) as recovered:
                result: Final = await recovered.execute("SELECT 1 AS value")
                row: Final = await result.fetchone()
                assert row is not None and row["value"] == 1
            raise RuntimeError("startup failure")
    assert pool.closed and recovered.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("configured,expected", ((0, "30s"), (60000, "30s"), (250, "250ms")))
async def test_database_pool_bounds_statements_without_raising_shorter_dsn_timeouts(configured: int, expected: str) -> None:
    from account_pool.repository import database_connection, database_pool

    url: Final = os.environ.get("ACCOUNT_POOL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires an isolated ACCOUNT_POOL_TEST_DATABASE_URL")
    scoped: Final = psycopg.conninfo.make_conninfo(url, options=f"-c statement_timeout={configured}")
    async with database_pool(scoped, max_size=1):
        async with database_connection(scoped) as connection:
            result: Final = await connection.execute("SHOW statement_timeout")
            row: Final = await result.fetchone()
            assert row is not None and row["statement_timeout"] == expected
        if configured == 250:
            with pytest.raises(psycopg.errors.QueryCanceled):
                async with database_connection(scoped) as timed_out:
                    await timed_out.execute("SELECT pg_sleep(1)")
            async with database_connection(scoped) as reused:
                assert reused is connection
                await reused.execute("SELECT 1")


@pytest.mark.asyncio
async def test_deleting_card_clears_its_leases_and_session_bindings() -> None:
    card_id: Final = uuid4()
    member_id: Final = uuid4()
    other_card_id: Final = uuid4()
    card_lease: Final = lease(card_id, member_id)
    unrelated_lease: Final = lease(other_card_id, member_id)
    state: Final = RuntimeState(
        environments={card_id, member_id, other_card_id},
        leases={
            card_lease.lease_id: (card_lease.account_id, card_lease.card_id),
            unrelated_lease.lease_id: (unrelated_lease.account_id, unrelated_lease.card_id),
        },
        sessions={"card": (card_id, member_id), "other": (other_card_id, member_id)},
        cooldowns={member_id},
    )

    await _delete_environment(connection(state), card_id)

    assert card_id not in state.environments
    assert state.leases == {unrelated_lease.lease_id: (member_id, other_card_id)}
    assert state.sessions == {"other": (other_card_id, member_id)}
    assert state.cooldowns == {member_id}


@pytest.mark.asyncio
async def test_deleting_candidate_clears_its_runtime_state() -> None:
    card_id: Final = uuid4()
    candidate_id: Final = uuid4()
    other_id: Final = uuid4()
    candidate_lease: Final = lease(card_id, candidate_id)
    unrelated_lease: Final = lease(card_id, other_id)
    state: Final = RuntimeState(
        environments={card_id, candidate_id, other_id},
        leases={
            candidate_lease.lease_id: (candidate_id, card_id),
            unrelated_lease.lease_id: (other_id, card_id),
        },
        sessions={"candidate": (card_id, candidate_id), "other": (card_id, other_id)},
        cooldowns={candidate_id, other_id},
    )

    await _delete_environment(connection(state), candidate_id)

    assert candidate_id not in state.environments
    assert state.leases == {unrelated_lease.lease_id: (other_id, card_id)}
    assert state.sessions == {"other": (card_id, other_id)}
    assert state.cooldowns == {other_id}


@pytest.mark.asyncio
async def test_releasing_deleted_account_does_not_restore_cooldown() -> None:
    deleted_id: Final = uuid4()
    active_lease: Final = lease(uuid4(), deleted_id)
    state: Final = RuntimeState(
        environments=set(),
        leases={active_lease.lease_id: (active_lease.account_id, active_lease.card_id)},
        sessions={},
        cooldowns=set(),
    )

    await _release_lease(connection(state), active_lease, cooldown_seconds=60, actual_tokens=None)

    assert state.leases == {}
    assert state.cooldowns == set()


@pytest.mark.asyncio
async def test_releasing_lease_twice_settles_token_budget_once() -> None:
    account_id: Final = uuid4()
    window_started_at: Final = datetime(2026, 9, 11, 8, tzinfo=timezone.utc)
    active_lease: Final = lease(uuid4(), account_id).model_copy(
        update={
            "reserved_tokens": 40,
            "budget_enabled": True,
            "budget_window_seconds": 3600,
            "budget_window_started_at": window_started_at,
        }
    )
    budget_key: Final = (account_id, 3600, window_started_at)
    state: Final = RuntimeState(
        environments={account_id},
        leases={active_lease.lease_id: (active_lease.account_id, active_lease.card_id)},
        sessions={},
        cooldowns=set(),
        settled_tokens={budget_key: 7},
    )

    await _release_lease(connection(state), active_lease, cooldown_seconds=0, actual_tokens=12)
    await _release_lease(connection(state), active_lease, cooldown_seconds=0, actual_tokens=12)

    assert state.settled_tokens[budget_key] == 19


@pytest.mark.asyncio
async def test_token_budget_uses_active_fixed_window_only() -> None:
    account_id: Final = uuid4()
    first_window: Final = datetime(2026, 9, 11, 8, tzinfo=timezone.utc)
    next_window: Final = datetime(2026, 9, 11, 9, tzinfo=timezone.utc)
    state: Final = BudgetState(
        settled_tokens={(account_id, 3600, first_window): 20},
        reservations=((account_id, 3600, first_window, 30),),
    )

    exact_limit: Final = await reserve_token_budget(
        budget_connection(state),
        account_id,
        limit=100,
        window_seconds=3600,
        requested_tokens=50,
        now=datetime(2026, 9, 11, 8, 59, 59, tzinfo=timezone.utc),
    )
    exhausted: Final = await reserve_token_budget(
        budget_connection(state),
        account_id,
        limit=100,
        window_seconds=3600,
        requested_tokens=51,
        now=datetime(2026, 9, 11, 8, 59, 59, tzinfo=timezone.utc),
    )
    reset: Final = await reserve_token_budget(
        budget_connection(state),
        account_id,
        limit=100,
        window_seconds=3600,
        requested_tokens=100,
        now=next_window,
    )

    assert exact_limit == first_window
    assert exhausted is None
    assert reset == next_window

@pytest.mark.asyncio
async def test_manager_pool_timeout_is_retryable_and_never_exposes_database_details(tmp_path) -> None:
    from account_pool.app import create_app
    from fastapi import Request
    from psycopg_pool import PoolTimeout
    from test_account_pool import _settings

    app = create_app(_settings(tmp_path))
    handler = app.exception_handlers[PoolTimeout]
    response = await handler(Request({"type": "http", "method": "GET", "path": "/api/environments", "headers": []}),
                             PoolTimeout("postgresql://fixture-secret@private-database.invalid:5432/private"))
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert b"fixture-secret" not in response.body and b"private-database" not in response.body


def test_settle_model_cooldowns_marks_rate_limit_and_prunes_expired_entries() -> None:
    now: Final = utc_now()
    active: Final = lease(uuid4(), uuid4()).model_copy(update={"started_at": now})
    expired_rate_limit: Final = ModelCooldown(
        model="expired-model", retry_at=now - timedelta(seconds=5), reason="rate_limit"
    )
    expired_tracking: Final = ModelCooldown(
        model="tracked-model",
        retry_at=now - timedelta(seconds=5),
        reason="timeout_tracking",
        consecutive_timeouts=1,
        timeout_state_at=now - timedelta(minutes=1),
    )
    settled: Final = settle_model_cooldowns(
        (expired_rate_limit, expired_tracking),
        active,
        60,
        False,
        now,
        failure_reason="rate_limit",
    )
    by_model = {item.model: item for item in settled}
    assert "expired-model" not in by_model
    assert by_model["tracked-model"] == expired_tracking
    assert by_model[active.model].reason == "rate_limit"
    assert by_model[active.model].retry_at == now + timedelta(seconds=60)
