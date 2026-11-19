"""用短事务持久化邮箱预占和换绑进度，运行锁不占用长事务。"""

from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from account_pool.email_rebinding.models import BindingState
from account_pool.repository import database_connection, database_operation_connection


@dataclass(frozen=True, slots=True)
class BindingTransaction:
    snapshot: BindingState
    connection: AsyncConnection[Mapping[str, object]]

    async def save(self, state: BindingState) -> None:
        await self.connection.execute(
            "INSERT INTO account_pool_email_rebinding (id,payload) VALUES (1,%s) "
            "ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload",
            (Jsonb(state.model_copy(update={"version": self.snapshot.version + 1}).model_dump(mode="json")),),
        )


class PostgresBindingRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url: Final = database_url

    async def initialize(self) -> None:
        async with database_connection(self.database_url) as connection:
            await connection.execute(
                "CREATE TABLE IF NOT EXISTS account_pool_email_rebinding "
                "(id integer PRIMARY KEY CHECK(id=1),payload jsonb NOT NULL)"
            )

    async def read(self) -> BindingState:
        async with database_connection(self.database_url) as connection:
            cursor: Final = await connection.execute("SELECT payload FROM account_pool_email_rebinding WHERE id=1")
            row: Final = await cursor.fetchone()
            return BindingState() if row is None else BindingState.model_validate(row["payload"])

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[BindingTransaction]:
        async with database_connection(self.database_url) as connection:
            await connection.execute("SET LOCAL lock_timeout = '2s'")
            await connection.execute("SELECT pg_advisory_xact_lock(714205998,1)")
            cursor: Final = await connection.execute("SELECT payload FROM account_pool_email_rebinding WHERE id=1")
            row: Final = await cursor.fetchone()
            yield BindingTransaction(
                BindingState() if row is None else BindingState.model_validate(row["payload"]), connection
            )

    @asynccontextmanager
    async def worker_lock(self) -> AsyncGenerator[bool]:
        async with database_operation_connection(self.database_url) as connection:
            await connection.set_autocommit(True)
            try:
                cursor: Final = await connection.execute("SELECT pg_try_advisory_lock(714205998,2) AS acquired")
                row: Final = await cursor.fetchone()
                acquired: Final = row is not None and row["acquired"] is True
                try:
                    yield acquired
                finally:
                    if acquired:
                        await connection.execute("SELECT pg_advisory_unlock(714205998,2)")
            finally:
                try:
                    await connection.set_autocommit(False)
                except BaseException:
                    await connection.close()
                    raise

    async def has_requests(self, environment_id: UUID) -> bool:
        async with database_connection(self.database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT 1 FROM account_pool_leases WHERE account_id=%s AND expires_at>now() LIMIT 1",
                (environment_id,),
            )
            return await cursor.fetchone() is not None
