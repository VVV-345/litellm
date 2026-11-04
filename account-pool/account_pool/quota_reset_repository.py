"""持久化额度兑换身份和结果，操作锁由现有凭据归属服务统一持有。"""

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from psycopg.types.json import Jsonb

from account_pool.domain import utc_now
from account_pool.gateway_contracts import FinishRequest, Lease
from account_pool.quota_reset_models import ResetOperation, ResetVerificationRecord, VerifiedAPICall
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
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS account_pool_quota_verifications (
                    environment_id uuid PRIMARY KEY REFERENCES account_pool_environments(id) ON DELETE CASCADE,
                    payload jsonb NOT NULL
                )
            """)
            await connection.execute("ALTER TABLE account_pool_quota_verifications ADD COLUMN IF NOT EXISTS api_call jsonb")

    async def save_verification(self, environment_id: UUID, verification: ResetVerificationRecord) -> None:
        async with database_connection(self.database_url) as connection:
            await connection.execute(
                "INSERT INTO account_pool_quota_verifications (environment_id, payload) VALUES (%s, %s) "
                "ON CONFLICT (environment_id) DO UPDATE SET payload = EXCLUDED.payload, api_call = NULL",
                (environment_id, Jsonb(verification.model_dump(mode="json"))),
            )

    async def latest_verification(self, environment_id: UUID) -> ResetVerificationRecord | None:
        async with database_connection(self.database_url) as connection:
            cursor = await connection.execute("SELECT payload FROM account_pool_quota_verifications WHERE environment_id = %s", (environment_id,))
            row = await cursor.fetchone()
            return None if row is None else ResetVerificationRecord.model_validate(row["payload"])

    async def latest_api_call(self, environment_id: UUID) -> VerifiedAPICall | None:
        async with database_connection(self.database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT api_call FROM account_pool_quota_verifications WHERE environment_id = %s "
                "AND api_call->>'verification_at' = payload->>'observed_at'", (environment_id,),
            )
            row: Final = await cursor.fetchone()
            return None if row is None else VerifiedAPICall.model_validate(row["api_call"])

    async def confirm_api_call(self, lease: Lease, request: FinishRequest) -> None:
        if not request.completed_successfully:
            return
        async with database_connection(self.database_url) as connection:
            # 锁住最近验证版本，旧在途请求和其他模型不能补写到新一次恢复记录。
            cursor: Final = await connection.execute(
                "SELECT payload, api_call FROM account_pool_quota_verifications WHERE environment_id = %s FOR UPDATE",
                (lease.account_id,),
            )
            row: Final = await cursor.fetchone()
            if row is None:
                return
            verification: Final = ResetVerificationRecord.model_validate(row["payload"])
            if not verification.result.passed or verification.model != lease.model or lease.started_at < verification.observed_at:
                return
            proof: Final = VerifiedAPICall.model_validate({
                "model": lease.model, "request_id": lease.request_id, "endpoint": request.endpoint,
                "verification_at": verification.observed_at, "observed_at": utc_now(),
            })
            await connection.execute(
                "UPDATE account_pool_quota_verifications SET api_call = %s WHERE environment_id = %s "
                "AND (api_call IS NULL OR api_call->>'verification_at' <> payload->>'observed_at')",
                (Jsonb(proof.model_dump(mode="json")), lease.account_id),
            )

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
