"""本文件验证删除环境与请求收尾会原子清理运行时租约、会话绑定和冷却状态。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from account_pool.domain import ChannelKind, SupplierKind, utc_now
from account_pool.gateway_contracts import Lease
from account_pool.gateway_repository import _release_lease
from account_pool.repository import _delete_environment


@dataclass
class RuntimeState:
    environments: set[UUID]
    leases: dict[UUID, tuple[UUID, UUID]]
    sessions: dict[str, tuple[UUID | None, UUID]]
    cooldowns: set[UUID]


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
            self.state.leases.pop(cast(UUID, params[0]), None)
            return ResultCursor()
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
        if statement.startswith("DELETE FROM account_pool_environments"):
            self.state.environments.discard(cast(UUID, params[0]))
            return ResultCursor()
        raise AssertionError(f"Unexpected query: {statement}")


def connection(state: RuntimeState) -> psycopg.AsyncConnection[dict[str, object]]:
    return cast(psycopg.AsyncConnection[dict[str, object]], RuntimeConnection(state))


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

    await _release_lease(connection(state), active_lease, cooldown_seconds=60)

    assert state.leases == {}
    assert state.cooldowns == set()
