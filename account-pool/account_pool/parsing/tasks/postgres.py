"""使用 PostgreSQL 保存不含凭证的解析任务状态、实例心跳和中断标记。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final, Literal, LiteralString, cast
from uuid import UUID

import psycopg
from psycopg import AsyncConnection, sql
from psycopg.rows import AsyncRowFactory, dict_row
from pydantic import AwareDatetime, ValidationError

from account_pool.models import FrozenModel
from account_pool.parsing.tasks.models import ParserTaskFailureCode, ParserTaskRecord, ParserTaskStatus
from account_pool.parsing.tasks.repository import (
    ParserTaskLoadResult,
    ParserTaskLoadSuccess,
    ParserTaskPersistenceFailure,
    ParserTaskPersistenceFailureCode,
    ParserTaskSweepResult,
    ParserTaskSweepSuccess,
    ParserTaskWriteResult,
    ParserTaskWriteSuccess,
)

_SCHEMA_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COLUMNS: Final[LiteralString] = """
task_id, channel_id, parser_run_id, provider_id, explicit_parser_id, openai_compatible,
status, owner_instance_id, actor_id, actor_role, request_id, created_at, heartbeat_at,
completed_at, failure_code
"""


class _TaskRow(FrozenModel):
    task_id: UUID
    channel_id: UUID
    parser_run_id: UUID
    provider_id: str
    explicit_parser_id: str | None
    openai_compatible: bool
    status: ParserTaskStatus
    owner_instance_id: UUID
    actor_id: str
    actor_role: str
    request_id: str
    created_at: AwareDatetime
    heartbeat_at: AwareDatetime
    completed_at: AwareDatetime | None
    failure_code: ParserTaskFailureCode | None


class PostgresParserTaskRepository:
    def __init__(self, database_url: str, schema: str = "public") -> None:
        if _SCHEMA_PATTERN.fullmatch(schema) is None:
            raise ValueError("schema must be a valid PostgreSQL identifier")
        self._database_url: Final = database_url
        self._schema: Final = schema

    async def create(self, record: ParserTaskRecord) -> ParserTaskWriteResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                channel: Final = await connection.execute(
                    'SELECT 1 FROM "LiteLLM_AccountPoolChannel" WHERE channel_id = %s',
                    (str(record.channel_id),),
                )
                if await channel.fetchone() is None:
                    return _failure(ParserTaskPersistenceFailureCode.CHANNEL_NOT_FOUND, retryable=False)
                await connection.execute(
                    """
                    INSERT INTO "LiteLLM_AccountPoolParserTask" (
                        task_id, channel_id, parser_run_id, provider_id, explicit_parser_id,
                        openai_compatible, status, owner_instance_id, actor_id, actor_role,
                        request_id, created_at, heartbeat_at, completed_at, failure_code
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    _record_values(record),
                )
                return ParserTaskWriteSuccess(status="created", record=record)
        except psycopg.IntegrityError:
            return _failure(ParserTaskPersistenceFailureCode.CONTENT_CONFLICT, retryable=False)
        except psycopg.Error:
            return _failure(ParserTaskPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def load(self, channel_id: UUID, task_id: UUID) -> ParserTaskLoadResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                cursor: Final = await connection.execute(
                    f'SELECT {_COLUMNS} FROM "LiteLLM_AccountPoolParserTask" WHERE task_id = %s AND channel_id = %s',
                    (str(task_id), str(channel_id)),
                )
                row: Final = await cursor.fetchone()
                if row is None:
                    return _failure(ParserTaskPersistenceFailureCode.TASK_NOT_FOUND, retryable=False)
                return ParserTaskLoadSuccess(record=_decode(row))
        except (ValidationError, ValueError):
            return _failure(ParserTaskPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _failure(ParserTaskPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def heartbeat(
        self,
        task_id: UUID,
        owner_instance_id: UUID,
        at: AwareDatetime,
    ) -> ParserTaskWriteResult:
        return await self._update(
            "heartbeat_at = %s",
            (at,),
            task_id=task_id,
            owner_instance_id=owner_instance_id,
        )

    async def finish(
        self,
        task_id: UUID,
        owner_instance_id: UUID,
        status: ParserTaskStatus,
        failure_code: ParserTaskFailureCode | None,
        at: AwareDatetime,
    ) -> ParserTaskWriteResult:
        if status not in (
            ParserTaskStatus.COMPLETED,
            ParserTaskStatus.FAILED,
            ParserTaskStatus.INTERRUPTED_REQUIRES_KEY,
        ):
            return _failure(ParserTaskPersistenceFailureCode.CONTENT_CONFLICT, retryable=False)
        return await self._update(
            "status = %s, failure_code = %s, completed_at = %s, heartbeat_at = %s",
            (status, failure_code, at, at),
            task_id=task_id,
            owner_instance_id=owner_instance_id,
        )

    async def sweep_stale(self, stale_before: AwareDatetime, at: AwareDatetime) -> ParserTaskSweepResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                cursor: Final = await connection.execute(
                    f"""
                    UPDATE "LiteLLM_AccountPoolParserTask"
                    SET status = %s, completed_at = %s, heartbeat_at = %s
                    WHERE status = %s AND heartbeat_at < %s
                    RETURNING {_COLUMNS}
                    """,
                    (
                        ParserTaskStatus.INTERRUPTED_REQUIRES_KEY,
                        at,
                        at,
                        ParserTaskStatus.RUNNING,
                        stale_before,
                    ),
                )
                rows: Final = tuple(await cursor.fetchall())
                return ParserTaskSweepSuccess(interrupted_tasks=tuple(_decode(row) for row in rows))
        except (KeyError, ValueError):
            return _sweep_failure(ParserTaskPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _sweep_failure(ParserTaskPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def _update(
        self,
        assignments: Literal[
            "heartbeat_at = %s",
            "status = %s, failure_code = %s, completed_at = %s, heartbeat_at = %s",
        ],
        values: tuple[object, ...],
        *,
        task_id: UUID,
        owner_instance_id: UUID,
    ) -> ParserTaskWriteResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                statement: Final = sql.SQL(
                    """
                    UPDATE "LiteLLM_AccountPoolParserTask"
                    SET {}
                    WHERE task_id = %s AND owner_instance_id = %s AND status = %s
                    RETURNING {}
                    """,
                ).format(sql.SQL(assignments), sql.SQL(_COLUMNS))
                cursor: Final = await connection.execute(
                    statement,
                    (*values, str(task_id), str(owner_instance_id), ParserTaskStatus.RUNNING),
                )
                row: Final = await cursor.fetchone()
                if row is None:
                    return _failure(ParserTaskPersistenceFailureCode.OWNERSHIP_CONFLICT, retryable=False)
                return ParserTaskWriteSuccess(status="updated", record=_decode(row))
        except (ValidationError, ValueError):
            return _failure(ParserTaskPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _failure(ParserTaskPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def _connect(self) -> AsyncConnection[Mapping[str, object]]:
        row_factory: Final = cast(AsyncRowFactory[Mapping[str, object]], dict_row)
        return await AsyncConnection[Mapping[str, object]].connect(self._database_url, row_factory=row_factory)

    async def _set_search_path(self, connection: AsyncConnection[Mapping[str, object]]) -> None:
        await connection.execute("SELECT set_config('search_path', %s, true)", (self._schema,))


def _record_values(record: ParserTaskRecord) -> tuple[object, ...]:
    return (
        str(record.task_id),
        str(record.channel_id),
        str(record.parser_run_id),
        record.provider_id,
        record.explicit_parser_id,
        record.openai_compatible,
        record.status,
        str(record.owner_instance_id),
        record.actor_id,
        record.actor_role,
        record.request_id,
        record.created_at,
        record.heartbeat_at,
        record.completed_at,
        record.failure_code,
    )


def _decode(value: Mapping[str, object]) -> ParserTaskRecord:
    row: Final = _TaskRow.model_validate(value)
    return ParserTaskRecord.model_validate(row.model_dump())


def _failure(code: ParserTaskPersistenceFailureCode, retryable: bool) -> ParserTaskPersistenceFailure:
    return ParserTaskPersistenceFailure(code=code, retryable=retryable)


def _sweep_failure(code: ParserTaskPersistenceFailureCode, retryable: bool) -> ParserTaskPersistenceFailure:
    return ParserTaskPersistenceFailure(code=code, retryable=retryable)
