"""本模块用 PostgreSQL 持久化不含明文 OAuth 凭据的环境元数据。"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Final
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from pydantic import TypeAdapter

from account_pool.domain import EnvironmentRecord, ProxyProfile

_RECORD_ADAPTER: Final = TypeAdapter(EnvironmentRecord)

_CREATE_SCHEMA: Final = (
    """
    CREATE TABLE IF NOT EXISTS account_pool_environments (
        id uuid PRIMARY KEY,
        payload jsonb NOT NULL,
        oauth_state text UNIQUE,
        updated_at timestamptz NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS account_pool_proxy_profiles (
        id text PRIMARY KEY,
        name text NOT NULL,
        proxy_url text NOT NULL
    )
    """,
)


@asynccontextmanager
async def _connection(database_url: str) -> AsyncGenerator[psycopg.AsyncConnection[Mapping[str, object]], None]:
    connection: Final = await psycopg.AsyncConnection.connect(database_url, row_factory=dict_row)
    async with connection:
        yield connection


class PostgresEnvironmentRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url: Final = database_url

    async def initialize(self) -> None:
        async with _connection(self._database_url) as connection:
            for statement in _CREATE_SCHEMA:
                await connection.execute(statement)

    async def list(self) -> tuple[EnvironmentRecord, ...]:
        async with _connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT payload FROM account_pool_environments ORDER BY updated_at DESC"
            )
            rows: Final[Sequence[Mapping[str, object]]] = await cursor.fetchall()
        return tuple(_RECORD_ADAPTER.validate_python(row["payload"]) for row in rows)

    async def get(self, environment_id: UUID) -> EnvironmentRecord | None:
        async with _connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT payload FROM account_pool_environments WHERE id = %s",
                (environment_id,),
            )
            row: Final = await cursor.fetchone()
        return None if row is None else _RECORD_ADAPTER.validate_python(row["payload"])

    async def find_by_oauth_state(self, state: str) -> EnvironmentRecord | None:
        async with _connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT payload FROM account_pool_environments WHERE oauth_state = %s",
                (state,),
            )
            row: Final = await cursor.fetchone()
        return None if row is None else _RECORD_ADAPTER.validate_python(row["payload"])

    async def save(self, record: EnvironmentRecord) -> EnvironmentRecord:
        payload: Final = record.model_dump(mode="json")
        async with _connection(self._database_url) as connection:
            await connection.execute(
                """
                INSERT INTO account_pool_environments (id, payload, oauth_state, updated_at)
                VALUES (%s, %s::jsonb, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    payload = EXCLUDED.payload,
                    oauth_state = EXCLUDED.oauth_state,
                    updated_at = EXCLUDED.updated_at
                """,
                (record.id, psycopg.types.json.Jsonb(payload), record.oauth_state, record.updated_at),
            )
        return record

    async def delete(self, environment_id: UUID) -> None:
        async with _connection(self._database_url) as connection:
            await connection.execute(
                "DELETE FROM account_pool_environments WHERE id = %s",
                (environment_id,),
            )

    async def save_if_version(
        self,
        record: EnvironmentRecord,
        expected_version: int,
    ) -> EnvironmentRecord | None:
        payload: Final = record.model_dump(mode="json")
        async with _connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                """
                UPDATE account_pool_environments
                SET payload = %s::jsonb, oauth_state = %s, updated_at = %s
                WHERE id = %s AND COALESCE((payload->>'version')::integer, 0) = %s
                """,
                (
                    psycopg.types.json.Jsonb(payload),
                    record.oauth_state,
                    record.updated_at,
                    record.id,
                    expected_version,
                ),
            )
        return record if cursor.rowcount == 1 else None


class PostgresProxyProfileRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url: Final = database_url

    async def list(self) -> tuple[ProxyProfile, ...]:
        async with _connection(self._database_url) as connection:
            cursor: Final = await connection.execute("SELECT id, name FROM account_pool_proxy_profiles ORDER BY name")
            rows: Final[Sequence[Mapping[str, object]]] = await cursor.fetchall()
        return tuple(ProxyProfile.model_validate(row) for row in rows)

    async def get_url(self, profile_id: str) -> str | None:
        async with _connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT id, name, proxy_url FROM account_pool_proxy_profiles WHERE id = %s",
                (profile_id,),
            )
            row: Final = await cursor.fetchone()
        if row is None:
            return None
        proxy_url: Final = row["proxy_url"]
        return proxy_url if isinstance(proxy_url, str) else None
