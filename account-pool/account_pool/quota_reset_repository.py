"""持久化额度兑换身份和结果，操作锁由现有凭据归属服务统一持有。"""

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from psycopg.types.json import Jsonb

from account_pool.quota_reset_models import ResetOperation
from account_pool.repository import database_connection


@dataclass(frozen=True, slots=True)
class PostgresResetStore:
    database_url: str

    async def initialize(self) -> None:
        async with database_connection(self.database_url) as connection:
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS account_pool_quota_resets (
                    request_id uuid PRIMARY KEY,
                    environment_id uuid NOT NULL REFERENCES account_pool_environments(id) ON DELETE CASCADE,
                    created_at timestamptz NOT NULL,
                    payload jsonb NOT NULL
                )
            """)
            await connection.execute("""
                CREATE INDEX IF NOT EXISTS account_pool_quota_resets_environment_idx
                ON account_pool_quota_resets (environment_id, created_at DESC)
            """)

    async def latest(self, environment_id: UUID) -> ResetOperation | None:
        async with database_connection(self.database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT payload FROM account_pool_quota_resets WHERE environment_id = %s "
                "ORDER BY created_at DESC, request_id DESC LIMIT 1",
                (environment_id,),
            )
            row: Final = await cursor.fetchone()
            return None if row is None else ResetOperation.model_validate(row["payload"])

    async def get(self, environment_id: UUID, request_id: UUID) -> ResetOperation | None:
        async with database_connection(self.database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT payload FROM account_pool_quota_resets WHERE environment_id = %s AND request_id = %s",
                (environment_id, request_id),
            )
            row: Final = await cursor.fetchone()
            return None if row is None else ResetOperation.model_validate(row["payload"])

    async def save(self, operation: ResetOperation) -> None:
        async with database_connection(self.database_url) as connection:
            await connection.execute(
                "INSERT INTO account_pool_quota_resets (request_id, environment_id, created_at, payload) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (request_id) DO UPDATE SET payload = EXCLUDED.payload "
                "WHERE account_pool_quota_resets.environment_id = EXCLUDED.environment_id",
                (
                    operation.request_id,
                    operation.environment_id,
                    operation.created_at,
                    Jsonb(operation.model_dump(mode="json")),
                ),
            )
