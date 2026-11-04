"""持久化有界自动化队列，短事务锁保护指纹绑定，独立领取锁保证跨进程串行。"""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Final

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from account_pool.oauth_automation.models import AutomationState
from account_pool.repository import database_connection


@dataclass(frozen=True, slots=True)
class AutomationTransaction:
    snapshot: AutomationState
    connection: AsyncConnection[Mapping[str, object]]

    async def save(self, state: AutomationState) -> AutomationState:
        updated: Final = state.model_copy(update={"version": self.snapshot.version + 1})
        await self.connection.execute(
            "INSERT INTO account_pool_oauth_automation (id, payload) VALUES (1, %s) "
            "ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload",
            (Jsonb(updated.model_dump(mode="json")),),
        )
        return updated


class PostgresAutomationRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url: Final = database_url

    async def initialize(self) -> None:
        async with database_connection(self._database_url) as connection:
            await connection.execute(
                "CREATE TABLE IF NOT EXISTS account_pool_oauth_automation ("
                "id integer PRIMARY KEY CHECK (id = 1), payload jsonb NOT NULL)"
            )

    @staticmethod
    async def _read(connection: AsyncConnection[Mapping[str, object]]) -> AutomationState:
        cursor: Final = await connection.execute("SELECT payload FROM account_pool_oauth_automation WHERE id = 1")
        row: Final = await cursor.fetchone()
        return AutomationState() if row is None else AutomationState.model_validate(row["payload"])

    async def read(self) -> AutomationState:
        async with database_connection(self._database_url) as connection:
            return await self._read(connection)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AutomationTransaction]:
        async with database_connection(self._database_url) as connection:
            await connection.execute("SELECT pg_advisory_xact_lock(714205994)")
            yield AutomationTransaction(await self._read(connection), connection)

    @asynccontextmanager
    async def worker_lock(self) -> AsyncIterator[bool]:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute("SELECT pg_try_advisory_xact_lock(714205995) AS acquired")
            row: Final = await cursor.fetchone()
            yield row is not None and row["acquired"] is True
