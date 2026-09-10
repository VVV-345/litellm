"""本模块使用 PostgreSQL 租约统一多进程并发、会话粘性和短暂故障冷却，不承载模型流量。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Final, Protocol
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from account_pool.card_keys import hash_card_key
from account_pool.domain import utc_now
from account_pool.gateway_contracts import AcquireRejected, AcquireRequest, Candidate, Lease, Resolution
from account_pool.repository import database_connection

_SCHEMA: Final = (
    """CREATE TABLE IF NOT EXISTS account_pool_leases (
        lease_id uuid PRIMARY KEY, account_id uuid NOT NULL, expires_at timestamptz NOT NULL, payload jsonb NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS account_pool_leases_account_idx ON account_pool_leases (account_id, expires_at)",
    """CREATE TABLE IF NOT EXISTS account_pool_sessions (
        binding_hash text PRIMARY KEY, card_id uuid NOT NULL, account_id uuid NOT NULL, expires_at timestamptz NOT NULL
    )""",
    "ALTER TABLE account_pool_sessions ADD COLUMN IF NOT EXISTS card_id uuid",
    # 旧会话无法从单向绑定摘要恢复卡片归属，升级时丢弃短期粘性，避免删除卡片后遗留绑定。
    "DELETE FROM account_pool_sessions WHERE card_id IS NULL",
    "ALTER TABLE account_pool_sessions ALTER COLUMN card_id SET NOT NULL",
    "CREATE INDEX IF NOT EXISTS account_pool_sessions_card_idx ON account_pool_sessions (card_id)",
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
    ) -> Lease | AcquireRejected: ...
    async def get(self, lease_id: UUID) -> Lease | None: ...
    async def release(self, lease: Lease, cooldown_seconds: int, actual_tokens: int | None) -> None: ...


class PostgresLeaseRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url: Final = database_url

    async def initialize(self) -> None:
        async with database_connection(self._database_url) as connection:
            for statement in _SCHEMA:
                await connection.execute(statement)
            await connection.execute(
                """CREATE TABLE IF NOT EXISTS account_pool_token_budget_windows (
                    account_id uuid NOT NULL REFERENCES account_pool_environments(id) ON DELETE CASCADE,
                    window_seconds integer NOT NULL,
                    window_started_at timestamptz NOT NULL,
                    settled_tokens bigint NOT NULL,
                    PRIMARY KEY (account_id, window_seconds, window_started_at)
                )"""
            )

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
    ) -> Lease | AcquireRejected:
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
                    return AcquireRejected(reason="session")
            for identifier, version, policy_version in sorted(
                {
                    (resolution.card_id, request.card_version, request.policy_version),
                    (candidate.id, request.account_version, request.account_policy_version),
                }
            ):
                if not await lock_version(connection, identifier, version, policy_version):
                    return AcquireRejected(reason="configuration")
            key_cursor: Final = await connection.execute(
                "SELECT key_id FROM account_pool_card_keys WHERE card_id = %s AND key_id = %s "
                "AND key_hash = %s AND revoked_at IS NULL FOR UPDATE",
                (resolution.card_id, resolution.key_id, hash_card_key(request.card_key)),
            )
            if await key_cursor.fetchone() is None:
                return AcquireRejected(reason="configuration")
            now: Final = utc_now()
            await connection.execute(
                "DELETE FROM account_pool_leases WHERE account_id = %s AND expires_at <= now()", (candidate.id,)
            )
            cooldown: Final = await connection.execute(
                "SELECT account_id FROM account_pool_runtime_cooldown WHERE account_id = %s AND expires_at > now()",
                (candidate.id,),
            )
            if await cooldown.fetchone() is not None:
                return AcquireRejected(reason="cooldown")
            counted: Final = await connection.execute(
                "SELECT count(*) AS used FROM account_pool_leases WHERE account_id = %s",
                (candidate.id,),
            )
            counted_row: Final = await counted.fetchone()
            if (
                counted_row is None
                or TypeAdapter(int).validate_python(counted_row["used"]) >= candidate.concurrency_limit
            ):
                return AcquireRejected(reason="concurrency")
            budget_limit: Final = candidate.policy.routing.token_budget_limit
            budget_window_seconds: Final = (
                candidate.policy.routing.token_budget_window_seconds if budget_limit is not None else None
            )
            budget_window_started_at: Final = (
                await reserve_token_budget(
                    connection,
                    candidate.id,
                    budget_limit,
                    candidate.policy.routing.token_budget_window_seconds,
                    request.estimated_tokens,
                    now,
                )
                if budget_limit is not None
                else None
            )
            if budget_limit is not None and budget_window_started_at is None:
                return AcquireRejected(reason="token_budget")
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
                reserved_tokens=request.estimated_tokens if budget_limit is not None else 0,
                budget_enabled=budget_limit is not None,
                budget_window_seconds=budget_window_seconds,
                budget_window_started_at=budget_window_started_at,
                attempt=request.attempt,
                routing_reason=request.routing_reason,
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
                    "INSERT INTO account_pool_sessions (binding_hash, card_id, account_id, expires_at) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (binding_hash) DO UPDATE SET "
                    "card_id = EXCLUDED.card_id, account_id = EXCLUDED.account_id, expires_at = EXCLUDED.expires_at",
                    (
                        binding_hash,
                        resolution.card_id,
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

    async def release(self, lease: Lease, cooldown_seconds: int, actual_tokens: int | None) -> None:
        async with database_connection(self._database_url) as connection:
            await _release_lease(connection, lease, cooldown_seconds, actual_tokens)


async def _release_lease(
    connection: psycopg.AsyncConnection[Mapping[str, object]],
    lease: Lease,
    cooldown_seconds: int,
    actual_tokens: int | None,
) -> None:
    environment: Final = await connection.execute(
        "SELECT id FROM account_pool_environments WHERE id = %s FOR UPDATE",
        (lease.account_id,),
    )
    environment_row: Final = await environment.fetchone()
    deleted: Final = await connection.execute(
        "DELETE FROM account_pool_leases WHERE lease_id = %s RETURNING lease_id", (lease.lease_id,)
    )
    deleted_row: Final = await deleted.fetchone()
    if (
        deleted_row is not None
        and lease.budget_window_seconds is not None
        and lease.budget_window_started_at is not None
    ):
        await connection.execute(
            "UPDATE account_pool_token_budget_windows SET settled_tokens = settled_tokens + %s "
            "WHERE account_id = %s AND window_seconds = %s AND window_started_at = %s",
            (
                actual_tokens or 0,
                lease.account_id,
                lease.budget_window_seconds,
                lease.budget_window_started_at,
            ),
        )
    if cooldown_seconds and environment_row is not None:
        await connection.execute(
            "INSERT INTO account_pool_runtime_cooldown VALUES (%s, %s) "
            "ON CONFLICT (account_id) DO UPDATE SET expires_at = GREATEST("
            "account_pool_runtime_cooldown.expires_at, EXCLUDED.expires_at)",
            (lease.account_id, utc_now() + timedelta(seconds=cooldown_seconds)),
        )


async def reserve_token_budget(
    connection: psycopg.AsyncConnection[Mapping[str, object]],
    account_id: UUID,
    limit: int,
    window_seconds: int,
    requested_tokens: int,
    now: datetime,
) -> datetime | None:
    window_started_at: Final = datetime.fromtimestamp(
        int(now.timestamp()) // window_seconds * window_seconds,
        tz=now.tzinfo,
    )
    budget: Final = await connection.execute(
        """
        INSERT INTO account_pool_token_budget_windows (
            account_id, window_seconds, window_started_at, settled_tokens
        ) VALUES (%s, %s, %s, 0)
        ON CONFLICT (account_id, window_seconds, window_started_at) DO UPDATE
        SET settled_tokens = account_pool_token_budget_windows.settled_tokens
        RETURNING window_started_at, settled_tokens
        """,
        (account_id, window_seconds, window_started_at),
    )
    budget_row: Final = await budget.fetchone()
    if budget_row is None:
        return None
    persisted_window_started_at: Final = TypeAdapter(datetime).validate_python(budget_row["window_started_at"])
    active: Final = await connection.execute(
        "SELECT COALESCE(sum((payload->>'reserved_tokens')::bigint), 0) AS reserved_tokens "
        "FROM account_pool_leases WHERE account_id = %s "
        "AND (payload->>'budget_window_seconds')::integer = %s "
        "AND (payload->>'budget_window_started_at')::timestamptz = %s",
        (account_id, window_seconds, persisted_window_started_at),
    )
    active_row: Final = await active.fetchone()
    settled_tokens: Final = TypeAdapter(int).validate_python(budget_row["settled_tokens"])
    reserved_tokens: Final = (
        0 if active_row is None else TypeAdapter(int).validate_python(active_row["reserved_tokens"])
    )
    return persisted_window_started_at if settled_tokens + reserved_tokens + requested_tokens <= limit else None


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
