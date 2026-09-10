"""本模块以 PostgreSQL 行锁和租约领取批量任务，重启后恢复尚未完成的账号操作。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Final, Protocol
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from account_pool.batch_models import BatchAuthorization, BatchClaim, BatchItem, BatchJob, BatchRequest, BatchTarget
from account_pool.repository import database_connection


class BatchRepository(Protocol):
    async def submit(self, request: BatchRequest) -> bool: ...
    async def list(self) -> tuple[BatchJob, ...]: ...
    async def get(self, job_id: UUID) -> BatchJob | None: ...
    async def claim(self) -> BatchClaim | None: ...
    async def finish(
        self, claim: BatchClaim, succeeded: bool, message: str, authorization: BatchAuthorization | None
    ) -> None: ...


class PostgresBatchRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url: Final = database_url

    async def initialize(self) -> None:
        async with database_connection(self.database_url) as connection:
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS account_pool_batch_jobs (
                    job_id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(), request jsonb NOT NULL
                )
            """)
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS account_pool_batch_items (
                    job_id uuid REFERENCES account_pool_batch_jobs(job_id) ON DELETE CASCADE,
                    account_id uuid NOT NULL, target jsonb NOT NULL, status text NOT NULL DEFAULT 'queued',
                    attempts integer NOT NULL DEFAULT 0, token uuid, expires_at timestamptz,
                    message text, authorization_result jsonb, finished_at timestamptz, PRIMARY KEY (job_id, account_id)
                )
            """)
            await connection.execute(
                "ALTER TABLE account_pool_batch_items ADD COLUMN IF NOT EXISTS authorization_result jsonb"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS account_pool_batch_claim_idx "
                "ON account_pool_batch_items (status, expires_at)"
            )

    async def submit(self, request: BatchRequest) -> bool:
        async with database_connection(self.database_url) as connection:
            await connection.execute(
                "INSERT INTO account_pool_batch_jobs (job_id, request) VALUES (%s, %s) ON CONFLICT (job_id) DO NOTHING",
                (request.job_id, Jsonb(request.model_dump(mode="json"))),
            )
            cursor: Final = await connection.execute(
                "SELECT request FROM account_pool_batch_jobs WHERE job_id = %s FOR UPDATE",
                (request.job_id,),
            )
            row: Final = await cursor.fetchone()
            if row is None or BatchRequest.model_validate(row["request"]) != request:
                return False
            for target in request.targets:
                await connection.execute(
                    "INSERT INTO account_pool_batch_items (job_id, account_id, target) VALUES (%s, %s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (request.job_id, target.account_id, Jsonb(target.model_dump(mode="json"))),
                )
            return True

    async def list(self) -> tuple[BatchJob, ...]:
        async with database_connection(self.database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT job_id FROM account_pool_batch_jobs ORDER BY created_at DESC LIMIT 30"
            )
            rows: Final = await cursor.fetchall()
        jobs: Final[tuple[BatchJob | None, ...]] = tuple(
            await asyncio.gather(*(self.get(TypeAdapter(UUID).validate_python(row["job_id"])) for row in rows))
        )
        return tuple(job for job in jobs if job is not None)

    async def get(self, job_id: UUID) -> BatchJob | None:
        async with database_connection(self.database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT * FROM account_pool_batch_jobs WHERE job_id = %s", (job_id,)
            )
            row: Final = await cursor.fetchone()
            if row is None:
                return None
            items: Final = await connection.execute(
                "SELECT account_id, status, attempts, message, authorization_result AS authorization, finished_at "
                "FROM account_pool_batch_items "
                "WHERE job_id = %s ORDER BY account_id",
                (job_id,),
            )
            results: Final = await items.fetchall()
            request: Final = BatchRequest.model_validate(row["request"])
            raw_created_at: object = row["created_at"]
            if not isinstance(raw_created_at, datetime) or raw_created_at.tzinfo is None:
                raise ValueError("Batch job created_at is not an aware datetime")
            created_at: Final = raw_created_at
            return BatchJob(
                job_id=job_id,
                action=request.action,
                created_at=created_at,
                items=tuple(BatchItem.model_validate(item) for item in results),
            )

    async def claim(self) -> BatchClaim | None:
        async with database_connection(self.database_url) as connection:
            # 旧工作进程只能用自己的 token 完成任务，过期领取不会覆盖新的执行结果。
            await connection.execute(
                "UPDATE account_pool_batch_items SET status = 'failed', finished_at = now(), "
                "message = 'Worker interrupted repeatedly; submit a new task' "
                "WHERE status = 'running' AND expires_at <= now() AND attempts >= 3"
            )
            cursor: Final = await connection.execute("""
                SELECT i.job_id, i.account_id, i.target, i.attempts, j.request FROM account_pool_batch_items i
                JOIN account_pool_batch_jobs j USING (job_id)
                WHERE i.status = 'queued' OR (i.status = 'running' AND i.expires_at <= now())
                ORDER BY j.created_at, i.account_id FOR UPDATE OF i SKIP LOCKED LIMIT 1
            """)
            row: Final = await cursor.fetchone()
            if row is None:
                return None
            token: Final = uuid4()
            await connection.execute(
                "UPDATE account_pool_batch_items SET status = 'running', attempts = attempts + 1, token = %s, "
                "expires_at = now() + interval '150 seconds' WHERE job_id = %s AND account_id = %s",
                (token, row["job_id"], row["account_id"]),
            )
            return BatchClaim(
                request=BatchRequest.model_validate(row["request"]),
                target=BatchTarget.model_validate(row["target"]),
                token=token,
                attempts=TypeAdapter(int).validate_python(row["attempts"]) + 1,
            )

    async def finish(
        self,
        claim: BatchClaim,
        succeeded: bool,
        message: str,
        authorization: BatchAuthorization | None,
    ) -> None:
        async with database_connection(self.database_url) as connection:
            await connection.execute(
                "UPDATE account_pool_batch_items SET status = %s, message = %s, authorization_result = %s, "
                "finished_at = now(), expires_at = NULL "
                "WHERE job_id = %s AND account_id = %s AND token = %s AND status = 'running'",
                (
                    "succeeded" if succeeded else "failed",
                    message,
                    None if authorization is None else Jsonb(authorization.model_dump(mode="json")),
                    claim.request.job_id,
                    claim.target.account_id,
                    claim.token,
                ),
            )
