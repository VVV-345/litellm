"""本模块用 PostgreSQL 持久化不含明文 OAuth 凭据的环境元数据。"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Final
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from account_pool.domain import EnvironmentRecord, ProxyProfile
from account_pool.oauth_browser import OAuthBrowserSession, OAuthBrowserSessionStatus

_RECORD_ADAPTER: Final = TypeAdapter(EnvironmentRecord)
_OAUTH_BROWSER_SESSION_ADAPTER: Final = TypeAdapter(OAuthBrowserSession)

_CREATE_SCHEMA: Final = (
    """
    CREATE TABLE IF NOT EXISTS account_pool_environments (
        id uuid PRIMARY KEY,
        payload jsonb NOT NULL,
        oauth_state text UNIQUE,
        oauth_state_consumed_at timestamptz,
        updated_at timestamptz NOT NULL
    )
    """,
    "ALTER TABLE account_pool_environments ADD COLUMN IF NOT EXISTS oauth_state_consumed_at timestamptz",
    "CREATE INDEX IF NOT EXISTS account_pool_environments_operation_id_idx ON account_pool_environments ((payload->>'operation_id'))",
    """
    CREATE TABLE IF NOT EXISTS account_pool_proxy_profiles (
        id text PRIMARY KEY,
        name text NOT NULL,
        proxy_url text NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS account_pool_oauth_browser_sessions (
        id uuid PRIMARY KEY,
        environment_id uuid NOT NULL REFERENCES account_pool_environments(id) ON DELETE CASCADE,
        status text NOT NULL,
        expires_at timestamptz NOT NULL,
        payload jsonb NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS account_pool_oauth_browser_sessions_environment_idx ON account_pool_oauth_browser_sessions (environment_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS account_pool_oauth_browser_sessions_active_environment_idx ON account_pool_oauth_browser_sessions (environment_id) WHERE status IN ('starting', 'active')",
    "CREATE UNIQUE INDEX IF NOT EXISTS account_pool_oauth_browser_sessions_active_callback_environment_idx ON account_pool_oauth_browser_sessions (environment_id) WHERE status IN ('starting', 'active', 'callback_pending')",
)


@asynccontextmanager
async def database_connection(database_url: str) -> AsyncGenerator[psycopg.AsyncConnection[Mapping[str, object]], None]:
    connection: Final = await psycopg.AsyncConnection[Mapping[str, object]].connect(
        database_url,
        row_factory=dict_row,
    )
    async with connection:
        yield connection


class PostgresEnvironmentRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url: Final = database_url

    async def initialize(self) -> None:
        async with database_connection(self._database_url) as connection:
            for statement in _CREATE_SCHEMA:
                await connection.execute(statement)

    async def list(self) -> tuple[EnvironmentRecord, ...]:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute("SELECT payload FROM account_pool_environments")
            rows: Final[Sequence[Mapping[str, object]]] = await cursor.fetchall()
        records: Final = tuple(_RECORD_ADAPTER.validate_python(row["payload"]) for row in rows)
        return tuple(sorted(records, key=lambda record: (record.created_at, record.id.int)))

    async def get(self, environment_id: UUID) -> EnvironmentRecord | None:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT payload FROM account_pool_environments WHERE id = %s",
                (environment_id,),
            )
            row: Final = await cursor.fetchone()
        return None if row is None else _RECORD_ADAPTER.validate_python(row["payload"])

    async def find_by_oauth_state(self, state: str) -> EnvironmentRecord | None:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT payload FROM account_pool_environments WHERE oauth_state = %s",
                (state,),
            )
            row: Final = await cursor.fetchone()
        return None if row is None else _RECORD_ADAPTER.validate_python(row["payload"])

    async def find_by_operation_id(self, operation_id: str) -> EnvironmentRecord | None:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT payload FROM account_pool_environments WHERE payload->>'operation_id' = %s LIMIT 1",
                (operation_id,),
            )
            row: Final = await cursor.fetchone()
        return None if row is None else _RECORD_ADAPTER.validate_python(row["payload"])

    async def save(self, record: EnvironmentRecord) -> EnvironmentRecord:
        payload: Final = record.model_dump(mode="json")
        async with database_connection(self._database_url) as connection:
            await connection.execute(
                """
                INSERT INTO account_pool_environments (
                    id, payload, oauth_state, oauth_state_consumed_at, updated_at
                )
                VALUES (%s, %s::jsonb, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    payload = jsonb_set(EXCLUDED.payload, '{model_cooldowns}', (
                        SELECT COALESCE(jsonb_agg(item), '[]'::jsonb) FROM (
                            SELECT DISTINCT ON (item->>'model') item
                            FROM (
                                SELECT item FROM jsonb_array_elements(
                                    COALESCE(account_pool_environments.payload->'model_cooldowns', '[]'::jsonb)
                                ) AS item
                                UNION ALL
                                SELECT item FROM jsonb_array_elements(
                                    COALESCE(EXCLUDED.payload->'model_cooldowns', '[]'::jsonb)
                                ) AS item WHERE (item->>'retry_at')::timestamptz > now()
                            ) AS states
                            ORDER BY item->>'model', (item->>'retry_at')::timestamptz DESC
                        ) AS cooldowns
                    )),
                    oauth_state = EXCLUDED.oauth_state,
                    oauth_state_consumed_at = EXCLUDED.oauth_state_consumed_at,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    record.id,
                    Jsonb(payload),
                    record.oauth_state,
                    record.oauth_state_consumed_at,
                    record.updated_at,
                ),
            )
        return record

    async def delete(self, environment_id: UUID) -> None:
        async with database_connection(self._database_url) as connection:
            await _delete_environment(connection, environment_id)

    async def save_if_version(
        self,
        record: EnvironmentRecord,
        expected_version: int,
    ) -> EnvironmentRecord | None:
        payload: Final = record.model_dump(mode="json")
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                """
                UPDATE account_pool_environments
                SET payload = jsonb_set(%s::jsonb, '{model_cooldowns}', (
                        SELECT COALESCE(jsonb_agg(item), '[]'::jsonb) FROM (
                            SELECT DISTINCT ON (item->>'model') item
                            FROM (
                                SELECT item FROM jsonb_array_elements(
                                    COALESCE(payload->'model_cooldowns', '[]'::jsonb)
                                ) AS item
                                UNION ALL
                                SELECT item FROM jsonb_array_elements(%s::jsonb) AS item
                                WHERE (item->>'retry_at')::timestamptz > now()
                            ) AS states
                            ORDER BY item->>'model', (item->>'retry_at')::timestamptz DESC
                        ) AS cooldowns
                    )),
                    oauth_state = %s,
                    oauth_state_consumed_at = %s,
                    updated_at = %s
                WHERE id = %s AND COALESCE((payload->>'version')::integer, 0) = %s
                """,
                (
                    Jsonb(payload),
                    Jsonb([item.model_dump(mode="json") for item in record.model_cooldowns]),
                    record.oauth_state,
                    record.oauth_state_consumed_at,
                    record.updated_at,
                    record.id,
                    expected_version,
                ),
            )
        return record if cursor.rowcount == 1 else None

    async def update_environment(
        self,
        environment_id: UUID,
        expected_version: int,
        environment: EnvironmentRecord,
    ) -> EnvironmentRecord | None:
        """按版本条件保存，供 Service 使用明确的冲突契约。"""
        if environment.id != environment_id:
            return None
        return await self.save_if_version(environment, expected_version)

    async def consume_oauth_state(self, state: str, consumed_at: datetime) -> EnvironmentRecord | None:
        """使用单条条件 UPDATE 消费 state，数据库层保证并发 callback 只有一个赢家。"""
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                """
                UPDATE account_pool_environments
                SET oauth_state_consumed_at = %s,
                    payload = jsonb_set(
                        jsonb_set(payload, '{oauth_state_consumed_at}', to_jsonb(%s::timestamptz), true),
                        '{version}',
                        to_jsonb(COALESCE((payload->>'version')::integer, 0) + 1),
                        true
                    ),
                    updated_at = %s
                WHERE oauth_state = %s
                  AND oauth_state_consumed_at IS NULL
                  AND COALESCE((payload->>'oauth_expires_at')::timestamptz, 'epoch'::timestamptz) > %s
                RETURNING payload
                """,
                (consumed_at, consumed_at, consumed_at, state, consumed_at),
            )
            row: Final = await cursor.fetchone()
        return None if row is None else _RECORD_ADAPTER.validate_python(row["payload"])


async def _delete_environment(
    connection: psycopg.AsyncConnection[Mapping[str, object]],
    environment_id: UUID,
) -> None:
    environment: Final = await connection.execute(
        "SELECT id FROM account_pool_environments WHERE id = %s FOR UPDATE",
        (environment_id,),
    )
    if await environment.fetchone() is None:
        return
    await connection.execute(
        "DELETE FROM account_pool_leases WHERE account_id = %s OR payload->>'card_id' = %s",
        (environment_id, str(environment_id)),
    )
    await connection.execute(
        "DELETE FROM account_pool_sessions WHERE account_id = %s OR card_id = %s",
        (environment_id, environment_id),
    )
    await connection.execute(
        "DELETE FROM account_pool_runtime_cooldown WHERE account_id = %s",
        (environment_id,),
    )
    await connection.execute("DELETE FROM account_pool_environments WHERE id = %s", (environment_id,))


class PostgresOAuthBrowserSessionRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url: Final = database_url

    async def create_if_absent(self, session: OAuthBrowserSession) -> bool:
        try:
            async with database_connection(self._database_url) as connection:
                await connection.execute(
                    """
                    UPDATE account_pool_oauth_browser_sessions
                    SET status = %s,
                        payload = jsonb_set(payload, '{status}', to_jsonb(%s::text), true)
                    WHERE environment_id = %s
                      AND status IN (%s, %s, %s)
                      AND expires_at <= %s
                    """,
                    (
                        OAuthBrowserSessionStatus.EXPIRED.value,
                        OAuthBrowserSessionStatus.EXPIRED.value,
                        session.environment_id,
                        OAuthBrowserSessionStatus.STARTING.value,
                        OAuthBrowserSessionStatus.ACTIVE.value,
                        OAuthBrowserSessionStatus.CALLBACK_PENDING.value,
                        session.created_at,
                    ),
                )
                await connection.execute(
                    """
                    INSERT INTO account_pool_oauth_browser_sessions (id, environment_id, status, expires_at, payload)
                    VALUES (%s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        session.id,
                        session.environment_id,
                        session.status.value,
                        session.expires_at,
                        Jsonb(session.model_dump(mode="json")),
                    ),
                )
        except psycopg.errors.UniqueViolation:
            return False
        return True

    async def get(self, session_id: UUID) -> OAuthBrowserSession | None:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT payload FROM account_pool_oauth_browser_sessions WHERE id = %s",
                (session_id,),
            )
            row: Final = await cursor.fetchone()
        return None if row is None else _OAUTH_BROWSER_SESSION_ADAPTER.validate_python(row["payload"])

    async def consume_ticket(
        self,
        session_id: UUID,
        ticket_digest: str,
        now: datetime,
    ) -> OAuthBrowserSession | None:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                """
                UPDATE account_pool_oauth_browser_sessions
                SET payload = jsonb_set(payload, '{ticket_consumed_at}', to_jsonb(%s::timestamptz), true)
                WHERE id = %s
                  AND status = %s
                  AND payload->>'ticket_digest' = %s
                  AND payload->>'ticket_consumed_at' IS NULL
                  AND expires_at > %s
                RETURNING payload
                """,
                (now, session_id, OAuthBrowserSessionStatus.ACTIVE.value, ticket_digest, now),
            )
            row: Final = await cursor.fetchone()
        return None if row is None else _OAUTH_BROWSER_SESSION_ADAPTER.validate_python(row["payload"])

    async def claim_callback(
        self,
        session_id: UUID,
        callback_token_digest: str,
        now: datetime,
    ) -> OAuthBrowserSession | None:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                """
                UPDATE account_pool_oauth_browser_sessions
                SET status = %s,
                    payload = jsonb_set(payload, '{status}', to_jsonb(%s::text), true)
                WHERE id = %s
                  AND status = %s
                  AND payload->>'callback_token_digest' = %s
                  AND expires_at > %s
                RETURNING payload
                """,
                (
                    OAuthBrowserSessionStatus.CALLBACK_PENDING.value,
                    OAuthBrowserSessionStatus.CALLBACK_PENDING.value,
                    session_id,
                    OAuthBrowserSessionStatus.ACTIVE.value,
                    callback_token_digest,
                    now,
                ),
            )
            row: Final = await cursor.fetchone()
        return None if row is None else _OAUTH_BROWSER_SESSION_ADAPTER.validate_python(row["payload"])

    async def transition(
        self,
        session_id: UUID,
        expected: tuple[OAuthBrowserSessionStatus, ...],
        target: OAuthBrowserSessionStatus,
    ) -> OAuthBrowserSession | None:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                """
                UPDATE account_pool_oauth_browser_sessions
                SET status = %s,
                    payload = jsonb_set(payload, '{status}', to_jsonb(%s::text), true)
                WHERE id = %s AND status = ANY(%s)
                RETURNING payload
                """,
                (target.value, target.value, session_id, [item.value for item in expected]),
            )
            row: Final = await cursor.fetchone()
        return None if row is None else _OAUTH_BROWSER_SESSION_ADAPTER.validate_python(row["payload"])

    async def expire_due(self, now: datetime) -> tuple[OAuthBrowserSession, ...]:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                """
                UPDATE account_pool_oauth_browser_sessions
                SET status = %s,
                    payload = jsonb_set(payload, '{status}', to_jsonb(%s::text), true)
                WHERE status = ANY(%s) AND expires_at <= %s
                RETURNING payload
                """,
                (
                    OAuthBrowserSessionStatus.EXPIRED.value,
                    OAuthBrowserSessionStatus.EXPIRED.value,
                    [
                        OAuthBrowserSessionStatus.STARTING.value,
                        OAuthBrowserSessionStatus.ACTIVE.value,
                        OAuthBrowserSessionStatus.CALLBACK_PENDING.value,
                    ],
                    now,
                ),
            )
            rows: Final[Sequence[Mapping[str, object]]] = await cursor.fetchall()
        return tuple(_OAUTH_BROWSER_SESSION_ADAPTER.validate_python(row["payload"]) for row in rows)

    async def list_cleanup_due(self, now: datetime) -> tuple[OAuthBrowserSession, ...]:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                """
                SELECT payload FROM account_pool_oauth_browser_sessions
                WHERE COALESCE((payload->>'cleanup_pending')::boolean, true)
                  AND (
                    status = ANY(%s)
                    OR (status = ANY(%s) AND expires_at <= %s)
                  )
                ORDER BY expires_at
                """,
                (
                    [
                        OAuthBrowserSessionStatus.COMPLETED.value,
                        OAuthBrowserSessionStatus.CANCELLED.value,
                        OAuthBrowserSessionStatus.FAILED.value,
                        OAuthBrowserSessionStatus.EXPIRED.value,
                    ],
                    [
                        OAuthBrowserSessionStatus.STARTING.value,
                        OAuthBrowserSessionStatus.ACTIVE.value,
                        OAuthBrowserSessionStatus.CALLBACK_PENDING.value,
                    ],
                    now,
                ),
            )
            rows: Final[Sequence[Mapping[str, object]]] = await cursor.fetchall()
        return tuple(_OAUTH_BROWSER_SESSION_ADAPTER.validate_python(row["payload"]) for row in rows)

    async def mark_cleaned(self, session_id: UUID) -> bool:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                """
                UPDATE account_pool_oauth_browser_sessions
                SET payload = jsonb_set(payload, '{cleanup_pending}', 'false'::jsonb, true)
                WHERE id = %s AND COALESCE((payload->>'cleanup_pending')::boolean, true)
                """,
                (session_id,),
            )
        return cursor.rowcount == 1


class PostgresProxyProfileRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url: Final = database_url

    async def list(self) -> tuple[ProxyProfile, ...]:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT id, name, split_part(proxy_url, ':', 1) AS protocol FROM account_pool_proxy_profiles ORDER BY name"
            )
            rows: Final[Sequence[Mapping[str, object]]] = await cursor.fetchall()
        return tuple(ProxyProfile.model_validate(row) for row in rows)

    async def get_url(self, profile_id: str) -> str | None:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT id, name, proxy_url FROM account_pool_proxy_profiles WHERE id = %s",
                (profile_id,),
            )
            row: Final = await cursor.fetchone()
        if row is None:
            return None
        proxy_url: Final = row["proxy_url"]
        return proxy_url if isinstance(proxy_url, str) else None

    async def upsert_gateways(self, gateways: Sequence[tuple[str, str, str]]) -> int:
        """写入或更新 Clash 网关条目；已存在的手工条目不受影响。"""
        if not gateways:
            return 0
        async with database_connection(self._database_url) as connection:
            async with connection.cursor() as cursor:
                await cursor.executemany(
                    """
                    INSERT INTO account_pool_proxy_profiles (id, name, proxy_url)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (id) DO UPDATE
                    SET name = EXCLUDED.name, proxy_url = EXCLUDED.proxy_url
                    WHERE account_pool_proxy_profiles.proxy_url <> EXCLUDED.proxy_url
                       OR account_pool_proxy_profiles.name <> EXCLUDED.name
                    """,
                    gateways,
                )
        return len(gateways)

    async def delete_gateway(self, profile_id: str) -> bool:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "DELETE FROM account_pool_proxy_profiles WHERE id = %s AND id LIKE 'clash-gateway-%%'",
                (profile_id,),
            )
        return cursor.rowcount == 1
