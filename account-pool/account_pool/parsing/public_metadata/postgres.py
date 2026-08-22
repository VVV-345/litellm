"""使用 PostgreSQL 实现公开元数据任务的幂等排队、竞争认领和重试。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final, LiteralString, cast
from uuid import UUID

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import AsyncRowFactory, dict_row
from pydantic import AwareDatetime, ValidationError

from account_pool.models import FrozenModel
from account_pool.parsing.public_metadata.models import (
    PublicMetadataTaskFailureCode,
    PublicMetadataTaskRecord,
    PublicMetadataTaskStatus,
)
from account_pool.parsing.public_metadata.repository import (
    PublicMetadataClaimResult,
    PublicMetadataClaimSuccess,
    PublicMetadataPersistenceFailure,
    PublicMetadataPersistenceFailureCode,
    PublicMetadataRecoveryResult,
    PublicMetadataRecoverySuccess,
    PublicMetadataScheduleResult,
    PublicMetadataScheduleSuccess,
    PublicMetadataWriteResult,
    PublicMetadataWriteSuccess,
)

_SCHEMA_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COLUMNS: Final[LiteralString] = """
task_id, channel_id, parser_run_id, provider_id, status, attempt_count, max_attempts,
owner_instance_id, next_attempt_at, created_at, updated_at, started_at, completed_at, failure_code
"""
_RETURNING_COLUMNS: Final[LiteralString] = """
task.task_id, task.channel_id, task.parser_run_id, task.provider_id, task.status,
task.attempt_count, task.max_attempts, task.owner_instance_id, task.next_attempt_at,
task.created_at, task.updated_at, task.started_at, task.completed_at, task.failure_code
"""


class _TaskRow(FrozenModel):
    task_id: UUID
    channel_id: UUID
    parser_run_id: UUID
    provider_id: str
    status: PublicMetadataTaskStatus
    attempt_count: int
    max_attempts: int
    owner_instance_id: UUID | None
    next_attempt_at: AwareDatetime
    created_at: AwareDatetime
    updated_at: AwareDatetime
    started_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    failure_code: PublicMetadataTaskFailureCode | None


class PostgresPublicMetadataTaskRepository:
    def __init__(self, database_url: str, schema: str = "public") -> None:
        if _SCHEMA_PATTERN.fullmatch(schema) is None:
            raise ValueError("schema must be a valid PostgreSQL identifier")
        self._database_url: Final = database_url
        self._schema: Final = schema

    async def schedule(
        self,
        record: PublicMetadataTaskRecord,
        refresh_after: AwareDatetime,
    ) -> PublicMetadataScheduleResult:
        if record.status != PublicMetadataTaskStatus.QUEUED:
            return _failure(PublicMetadataPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                cursor: Final = await connection.execute(
                    f"""
                    INSERT INTO "LiteLLM_AccountPoolPublicMetadataTask" ({_COLUMNS})
                    SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM "LiteLLM_AccountPoolPublicMetadataTask"
                        WHERE channel_id = %s
                          AND (
                              status IN ('queued', 'running', 'retry_wait')
                              OR updated_at >= %s
                          )
                    )
                    ON CONFLICT DO NOTHING
                    RETURNING {_COLUMNS}
                    """,
                    (*_record_values(record), str(record.channel_id), refresh_after),
                )
                row: Final = await cursor.fetchone()
                if row is None:
                    return PublicMetadataScheduleSuccess(status="unchanged")
                return PublicMetadataScheduleSuccess(status="created", record=_decode(row))
        except (ValidationError, ValueError):
            return _failure(PublicMetadataPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _failure(PublicMetadataPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def recover_stale(
        self,
        stale_before: AwareDatetime,
        at: AwareDatetime,
    ) -> PublicMetadataRecoveryResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                cursor: Final = await connection.execute(
                    f"""
                    UPDATE "LiteLLM_AccountPoolPublicMetadataTask"
                    SET status = CASE WHEN attempt_count >= max_attempts THEN 'failed' ELSE 'retry_wait' END,
                        owner_instance_id = NULL,
                        next_attempt_at = %s,
                        updated_at = %s,
                        completed_at = CASE WHEN attempt_count >= max_attempts THEN %s ELSE NULL END,
                        failure_code = %s
                    WHERE status = 'running' AND updated_at < %s
                    RETURNING {_COLUMNS}
                    """,
                    (at, at, at, PublicMetadataTaskFailureCode.WORKER_LOST, stale_before),
                )
                return PublicMetadataRecoverySuccess(records=tuple(_decode(row) for row in await cursor.fetchall()))
        except (ValidationError, ValueError):
            return _failure(PublicMetadataPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _failure(PublicMetadataPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def claim_next(
        self,
        owner_instance_id: UUID,
        at: AwareDatetime,
    ) -> PublicMetadataClaimResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                cursor: Final = await connection.execute(
                    f"""
                    WITH candidate AS (
                        SELECT task_id
                        FROM "LiteLLM_AccountPoolPublicMetadataTask"
                        WHERE status IN ('queued', 'retry_wait')
                          AND next_attempt_at <= %s
                          AND attempt_count < max_attempts
                        ORDER BY next_attempt_at, created_at, task_id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE "LiteLLM_AccountPoolPublicMetadataTask" AS task
                    SET status = 'running',
                        attempt_count = task.attempt_count + 1,
                        owner_instance_id = %s,
                        started_at = COALESCE(task.started_at, %s),
                        updated_at = %s,
                        failure_code = NULL
                    FROM candidate
                    WHERE task.task_id = candidate.task_id
                    RETURNING {_RETURNING_COLUMNS}
                    """,
                    (at, str(owner_instance_id), at, at),
                )
                row: Final = await cursor.fetchone()
                if row is None:
                    return PublicMetadataClaimSuccess(status="empty")
                return PublicMetadataClaimSuccess(status="claimed", record=_decode(row))
        except (ValidationError, ValueError):
            return _failure(PublicMetadataPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _failure(PublicMetadataPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def heartbeat(
        self,
        task_id: UUID,
        owner_instance_id: UUID,
        at: AwareDatetime,
    ) -> PublicMetadataWriteResult:
        return await self._update_owned(
            task_id=task_id,
            owner_instance_id=owner_instance_id,
            assignments="updated_at = %s",
            values=(at,),
        )

    async def retry(
        self,
        task_id: UUID,
        owner_instance_id: UUID,
        parser_run_id: UUID,
        failure_code: PublicMetadataTaskFailureCode,
        next_attempt_at: AwareDatetime,
        at: AwareDatetime,
    ) -> PublicMetadataWriteResult:
        return await self._update_owned(
            task_id=task_id,
            owner_instance_id=owner_instance_id,
            assignments=(
                "status = 'retry_wait', parser_run_id = %s, owner_instance_id = NULL, "
                "next_attempt_at = %s, updated_at = %s, failure_code = %s"
            ),
            values=(str(parser_run_id), next_attempt_at, at, failure_code),
        )

    async def finish(
        self,
        task_id: UUID,
        owner_instance_id: UUID,
        status: PublicMetadataTaskStatus,
        failure_code: PublicMetadataTaskFailureCode | None,
        at: AwareDatetime,
    ) -> PublicMetadataWriteResult:
        if status not in (PublicMetadataTaskStatus.COMPLETED, PublicMetadataTaskStatus.FAILED):
            return _failure(PublicMetadataPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        if (status == PublicMetadataTaskStatus.FAILED) != (failure_code is not None):
            return _failure(PublicMetadataPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        return await self._update_owned(
            task_id=task_id,
            owner_instance_id=owner_instance_id,
            assignments=(
                "status = %s, owner_instance_id = NULL, updated_at = %s, completed_at = %s, failure_code = %s"
            ),
            values=(status, at, at, failure_code),
        )

    async def _update_owned(
        self,
        *,
        task_id: UUID,
        owner_instance_id: UUID,
        assignments: LiteralString,
        values: tuple[object, ...],
    ) -> PublicMetadataWriteResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                cursor: Final = await connection.execute(
                    f"""
                    UPDATE "LiteLLM_AccountPoolPublicMetadataTask"
                    SET {assignments}
                    WHERE task_id = %s AND owner_instance_id = %s AND status = 'running'
                    RETURNING {_COLUMNS}
                    """,
                    (*values, str(task_id), str(owner_instance_id)),
                )
                row: Final = await cursor.fetchone()
                if row is None:
                    return _failure(PublicMetadataPersistenceFailureCode.OWNERSHIP_CONFLICT, retryable=False)
                return PublicMetadataWriteSuccess(record=_decode(row))
        except (ValidationError, ValueError):
            return _failure(PublicMetadataPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _failure(PublicMetadataPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def _connect(self) -> AsyncConnection[Mapping[str, object]]:
        row_factory: Final = cast(AsyncRowFactory[Mapping[str, object]], dict_row)
        return await AsyncConnection[Mapping[str, object]].connect(self._database_url, row_factory=row_factory)

    async def _set_search_path(self, connection: AsyncConnection[Mapping[str, object]]) -> None:
        await connection.execute("SELECT set_config('search_path', %s, true)", (self._schema,))


def _record_values(record: PublicMetadataTaskRecord) -> tuple[object, ...]:
    return (
        str(record.task_id),
        str(record.channel_id),
        str(record.parser_run_id),
        record.provider_id,
        record.status,
        record.attempt_count,
        record.max_attempts,
        None if record.owner_instance_id is None else str(record.owner_instance_id),
        record.next_attempt_at,
        record.created_at,
        record.updated_at,
        record.started_at,
        record.completed_at,
        record.failure_code,
    )


def _decode(value: Mapping[str, object]) -> PublicMetadataTaskRecord:
    row: Final = _TaskRow.model_validate(value)
    return PublicMetadataTaskRecord.model_validate(row.model_dump())


def _failure(
    code: PublicMetadataPersistenceFailureCode,
    retryable: bool,
) -> PublicMetadataPersistenceFailure:
    return PublicMetadataPersistenceFailure(code=code, retryable=retryable)
