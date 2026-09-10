"""本文件验证删除环境与请求收尾会原子清理运行时租约、会话绑定和冷却状态。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Final, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from account_pool.domain import ChannelKind, SupplierKind, utc_now
from account_pool.gateway_contracts import Lease
from account_pool.gateway_repository import _release_lease, reserve_token_budget
from account_pool.repository import _delete_environment


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
        if statement.startswith("SELECT id FROM account_pool_environments"):
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
