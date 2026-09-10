"""本模块使用 PostgreSQL 租约统一多进程并发、会话粘性和短暂故障冷却，不承载模型流量。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Final, Protocol
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from account_pool.card_keys import hash_card_key
from account_pool.domain import utc_now
from account_pool.gateway_contracts import AcquireRequest, Candidate, Lease, Resolution
from account_pool.repository import database_connection

_SCHEMA: Final = (
    """CREATE TABLE IF NOT EXISTS account_pool_leases (
        lease_id uuid PRIMARY KEY, account_id uuid NOT NULL, expires_at timestamptz NOT NULL, payload jsonb NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS account_pool_leases_account_idx ON account_pool_leases (account_id, expires_at)",
    """CREATE TABLE IF NOT EXISTS account_pool_sessions (
        binding_hash text PRIMARY KEY, account_id uuid NOT NULL, expires_at timestamptz NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS account_pool_runtime_cooldown (
        account_id uuid PRIMARY KEY, expires_at timestamptz NOT NULL
    )""",
)


class CooldownRepository(Protocol):
    async def clear_cooldown(self, account_id: UUID) -> None: ...


class LeaseRepository(CooldownRepository, Protocol):
    async def sticky(self, binding_hash: str | None) -> UUID | None: ...
    async def cooling(self) -> tuple[UUID, ...]: ...
    async def acquire(
        self,
        request: AcquireRequest,
        resolution: Resolution,
        candidate: Candidate,
        binding_hash: str | None,
    ) -> Lease | None: ...
    async def get(self, lease_id: UUID) -> Lease | None: ...
    async def release(self, lease: Lease, cooldown_seconds: int) -> None: ...


class PostgresLeaseRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url: Final = database_url

    async def initialize(self) -> None:
        async with database_connection(self._database_url) as connection:
            for statement in _SCHEMA:
                await connection.execute(statement)

    async def sticky(self, binding_hash: str | None) -> UUID | None:
        if binding_hash is None:
            return None
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT account_id FROM account_pool_sessions WHERE binding_hash = %s AND expires_at > now()",
                (binding_hash,),
            )
            row: Final = await cursor.fetchone()
        return None if row is None else TypeAdapter(UUID).validate_python(row["account_id"])

    async def cooling(self) -> tuple[UUID, ...]:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT account_id FROM account_pool_runtime_cooldown WHERE expires_at > now()",
            )
            rows: Final = await cursor.fetchall()
        return tuple(TypeAdapter(UUID).validate_python(row["account_id"]) for row in rows)

    async def clear_cooldown(self, account_id: UUID) -> None:
        async with database_connection(self._database_url) as connection:
            await connection.execute("DELETE FROM account_pool_runtime_cooldown WHERE account_id = %s", (account_id,))

    async def acquire(
        self,
        request: AcquireRequest,
        resolution: Resolution,
        candidate: Candidate,
        binding_hash: str | None,
    ) -> Lease | None:
        async with database_connection(self._database_url) as connection:
            # 锁顺序固定；策略更新也锁环境行，快照版本变化时拒绝使用旧授权或旧配置。
            if binding_hash is not None:
                await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (binding_hash,))
                binding_cursor: Final = await connection.execute(
                    "SELECT account_id FROM account_pool_sessions WHERE binding_hash = %s AND expires_at > now()",
                    (binding_hash,),
                )
                binding_row: Final = await binding_cursor.fetchone()
                if (
                    binding_row is not None
                    and binding_row["account_id"] != candidate.id
                    and not request.allow_session_rebind
                ):
                    return None
            for identifier, version, policy_version in sorted(
                {
                    (resolution.card_id, request.card_version, request.policy_version),
                    (candidate.id, request.account_version, request.account_policy_version),
                }
            ):
                if not await lock_version(connection, identifier, version, policy_version):
                    return None
            key_cursor: Final = await connection.execute(
                "SELECT key_id FROM account_pool_card_keys WHERE card_id = %s AND key_id = %s "
                "AND key_hash = %s AND revoked_at IS NULL FOR UPDATE",
                (resolution.card_id, resolution.key_id, hash_card_key(request.card_key)),
            )
            if await key_cursor.fetchone() is None:
                return None
            await connection.execute(
                "DELETE FROM account_pool_leases WHERE account_id = %s AND expires_at <= now()", (candidate.id,)
            )
            cooldown: Final = await connection.execute(
                "SELECT account_id FROM account_pool_runtime_cooldown WHERE account_id = %s AND expires_at > now()",
                (candidate.id,),
            )
            if await cooldown.fetchone() is not None:
                return None
            counted: Final = await connection.execute(
                "SELECT count(*) AS used FROM account_pool_leases WHERE account_id = %s",
                (candidate.id,),
            )
            counted_row: Final = await counted.fetchone()
            if (
                counted_row is None
                or TypeAdapter(int).validate_python(counted_row["used"]) >= candidate.concurrency_limit
            ):
                return None
            now: Final = utc_now()
            lease: Final = Lease(
                lease_id=uuid4(),
                account_id=candidate.id,
                card_id=resolution.card_id,
                key_id=resolution.key_id,
                request_id=request.request_id,
                model=request.model,
                channel=candidate.channel,
                supplier=candidate.supplier,
                started_at=now,
                attempt=request.attempt,
            )
            await connection.execute(
                "INSERT INTO account_pool_leases VALUES (%s, %s, %s, %s)",
                (
                    lease.lease_id,
                    candidate.id,
                    now + timedelta(seconds=request.timeout_seconds + 30),
                    Jsonb(lease.model_dump(mode="json")),
                ),
            )
            if binding_hash is not None:
                await connection.execute(
                    "INSERT INTO account_pool_sessions VALUES (%s, %s, %s) "
                    "ON CONFLICT (binding_hash) DO UPDATE SET account_id = EXCLUDED.account_id, "
                    "expires_at = EXCLUDED.expires_at",
                    (
                        binding_hash,
                        candidate.id,
                        now + timedelta(seconds=resolution.policy.routing.session_affinity_ttl),
                    ),
                )
            await connection.execute(
                "UPDATE account_pool_card_keys SET last_used_at = %s WHERE key_id = %s",
                (now, resolution.key_id),
            )
            return lease

    async def get(self, lease_id: UUID) -> Lease | None:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT payload FROM account_pool_leases WHERE lease_id = %s",
                (lease_id,),
            )
            row: Final = await cursor.fetchone()
        return None if row is None else Lease.model_validate(row["payload"])

    async def release(self, lease: Lease, cooldown_seconds: int) -> None:
        async with database_connection(self._database_url) as connection:
            await connection.execute("DELETE FROM account_pool_leases WHERE lease_id = %s", (lease.lease_id,))
            if cooldown_seconds:
                await connection.execute(
                    "INSERT INTO account_pool_runtime_cooldown VALUES (%s, %s) "
                    "ON CONFLICT (account_id) DO UPDATE SET expires_at = GREATEST("
                    "account_pool_runtime_cooldown.expires_at, EXCLUDED.expires_at)",
                    (lease.account_id, utc_now() + timedelta(seconds=cooldown_seconds)),
                )


async def lock_version(
    connection: psycopg.AsyncConnection[Mapping[str, object]],
    identifier: UUID,
    version: int,
    policy_version: int,
) -> bool:
    cursor: Final = await connection.execute(
        "SELECT (payload->>'version')::integer AS version FROM account_pool_environments WHERE id = %s FOR UPDATE",
        (identifier,),
    )
    row: Final = await cursor.fetchone()
    if row is None or row["version"] != version:
        return False
    policy_cursor: Final = await connection.execute(
        "SELECT version FROM account_pool_policies WHERE card_id = %s",
        (identifier,),
    )
    policy_row: Final = await policy_cursor.fetchone()
    if (0 if policy_row is None else policy_row["version"]) != policy_version:
        return False
    return True
