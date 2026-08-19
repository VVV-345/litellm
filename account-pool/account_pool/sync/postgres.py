"""使用 PostgreSQL 持久化并安全转换渠道同步操作状态。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final, Literal, LiteralString, cast
from uuid import UUID

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import AsyncRowFactory, dict_row
from psycopg.types.json import Jsonb
from pydantic import AwareDatetime, ValidationError, model_validator
from typing_extensions import Self

from account_pool.models import FrozenModel
from account_pool.sync.models import (
    ChannelDesiredState,
    DeleteMode,
    SafeSyncFailure,
    SyncAction,
    SyncOperation,
    SyncStatus,
)
from account_pool.sync.repository import (
    SyncOperationListResult,
    SyncOperationListSuccess,
    SyncOperationLoadResult,
    SyncOperationLoadSuccess,
    SyncOperationPersistenceFailure,
    SyncOperationPersistenceFailureCode,
    SyncOperationWriteResult,
    SyncOperationWriteSuccess,
    same_operation_request,
)

_SCHEMA_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COLUMNS: Final = """
operation_id, idempotency_key, channel_id, action, status, delete_mode,
desired_schema_version, desired_payload, attempt_count, requires_key,
failure_code, failure_message, created_at, updated_at, applied_at
"""
_RETRYABLE_STATUSES: Final = (
    SyncStatus.PENDING_CREATE,
    SyncStatus.PENDING_UPDATE,
    SyncStatus.PENDING_DELETE,
    SyncStatus.FAILED,
)


class _OperationRow(FrozenModel):
    operation_id: UUID
    idempotency_key: str
    channel_id: UUID
    action: SyncAction
    status: SyncStatus
    delete_mode: DeleteMode | None
    desired_schema_version: Literal[1]
    desired_payload: object
    attempt_count: int
    requires_key: bool
    failure_code: str | None
    failure_message: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    applied_at: AwareDatetime | None

    @model_validator(mode="after")
    def validate_failure_columns(self) -> Self:
        if (self.failure_code is None) != (self.failure_message is None):
            raise ValueError("stored failure code and message must both be present or absent")
        return self


class PostgresSyncOperationRepository:
    def __init__(self, database_url: str, schema: str = "public") -> None:
        if _SCHEMA_PATTERN.fullmatch(schema) is None:
            raise ValueError("schema must be a valid PostgreSQL identifier")
        self._database_url: Final = database_url
        self._schema: Final = schema

    async def create(self, operation: SyncOperation) -> SyncOperationWriteResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                cursor: Final = await connection.execute(
                    """
                    INSERT INTO "LiteLLM_AccountPoolSyncOperation" (
                        operation_id, idempotency_key, channel_id, action, status, delete_mode,
                        desired_schema_version, desired_payload, attempt_count, requires_key,
                        failure_code, failure_message, created_at, updated_at, applied_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING operation_id
                    """,
                    _operation_values(operation),
                )
                if await cursor.fetchone() is not None:
                    return SyncOperationWriteSuccess(status="created", operation=operation)
                existing_result: Final = await self._load_by_key(connection, operation.idempotency_key)
                if isinstance(existing_result, SyncOperationPersistenceFailure):
                    return existing_result
                if same_operation_request(existing_result.operation, operation):
                    return SyncOperationWriteSuccess(status="existing", operation=existing_result.operation)
                return _failure(SyncOperationPersistenceFailureCode.IDEMPOTENCY_CONFLICT, retryable=False)
        except psycopg.IntegrityError:
            return _failure(SyncOperationPersistenceFailureCode.STATE_CONFLICT, retryable=False)
        except (ValidationError, ValueError, KeyError, TypeError):
            return _failure(SyncOperationPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _failure(SyncOperationPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def load(self, operation_id: UUID) -> SyncOperationLoadResult:
        return await self._load("operation_id", str(operation_id))

    async def load_by_idempotency_key(self, idempotency_key: str) -> SyncOperationLoadResult:
        return await self._load("idempotency_key", idempotency_key)

    async def list_pending_and_failed(self, limit: int = 100) -> SyncOperationListResult:
        if limit < 1:
            return _failure(SyncOperationPersistenceFailureCode.STATE_CONFLICT, retryable=False)
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                cursor: Final = await connection.execute(
                    f"""
                    SELECT {_COLUMNS}
                    FROM "LiteLLM_AccountPoolSyncOperation"
                    WHERE status IN (%s, %s, %s, %s)
                    ORDER BY created_at, operation_id
                    LIMIT %s
                    """,
                    (*tuple(status.value for status in _RETRYABLE_STATUSES), limit),
                )
                rows: Final = tuple(await cursor.fetchall())
                return SyncOperationListSuccess(operations=tuple(decode_operation_row(row) for row in rows))
        except (ValidationError, ValueError, KeyError, TypeError):
            return _failure(SyncOperationPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _failure(SyncOperationPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def record_attempt(self, operation_id: UUID, at: AwareDatetime) -> SyncOperationWriteResult:
        return await self._transition(
            operation_id=operation_id,
            assignments="attempt_count = attempt_count + 1, updated_at = %s",
            values=(at,),
        )

    async def mark_applied(self, operation_id: UUID, at: AwareDatetime) -> SyncOperationWriteResult:
        return await self._transition(
            operation_id=operation_id,
            assignments=(
                "status = 'applied', failure_code = NULL, failure_message = NULL, "
                "requires_key = FALSE, applied_at = %s, updated_at = %s"
            ),
            values=(at, at),
        )

    async def mark_failed(
        self,
        operation_id: UUID,
        failure: SafeSyncFailure,
        requires_key: bool,
        at: AwareDatetime,
    ) -> SyncOperationWriteResult:
        return await self._transition(
            operation_id=operation_id,
            assignments=(
                "status = 'failed', failure_code = %s, failure_message = %s, "
                "requires_key = %s, applied_at = NULL, updated_at = %s"
            ),
            values=(failure.code, failure.message, requires_key, at),
        )

    async def _load(self, column: Literal["operation_id", "idempotency_key"], value: str) -> SyncOperationLoadResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                cursor: Final = await connection.execute(
                    f'SELECT {_COLUMNS} FROM "LiteLLM_AccountPoolSyncOperation" WHERE {column} = %s',
                    (value,),
                )
                row: Final = await cursor.fetchone()
                if row is None:
                    return _failure(SyncOperationPersistenceFailureCode.OPERATION_NOT_FOUND, retryable=False)
                return SyncOperationLoadSuccess(operation=decode_operation_row(row))
        except (ValidationError, ValueError, KeyError, TypeError):
            return _failure(SyncOperationPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _failure(SyncOperationPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def _load_by_key(
        self,
        connection: AsyncConnection[Mapping[str, object]],
        idempotency_key: str,
    ) -> SyncOperationLoadResult:
        cursor: Final = await connection.execute(
            f'SELECT {_COLUMNS} FROM "LiteLLM_AccountPoolSyncOperation" WHERE idempotency_key = %s',
            (idempotency_key,),
        )
        row: Final = await cursor.fetchone()
        if row is None:
            return _failure(SyncOperationPersistenceFailureCode.OPERATION_NOT_FOUND, retryable=False)
        return SyncOperationLoadSuccess(operation=decode_operation_row(row))

    async def _transition(
        self,
        operation_id: UUID,
        assignments: LiteralString,
        values: tuple[object, ...],
    ) -> SyncOperationWriteResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                cursor: Final = await connection.execute(
                    f"""
                    UPDATE "LiteLLM_AccountPoolSyncOperation"
                    SET {assignments}
                    WHERE operation_id = %s AND status IN (%s, %s, %s, %s)
                    RETURNING {_COLUMNS}
                    """,
                    (*values, str(operation_id), *tuple(status.value for status in _RETRYABLE_STATUSES)),
                )
                row: Final = await cursor.fetchone()
                if row is not None:
                    return SyncOperationWriteSuccess(status="updated", operation=decode_operation_row(row))
                exists_cursor: Final = await connection.execute(
                    'SELECT 1 FROM "LiteLLM_AccountPoolSyncOperation" WHERE operation_id = %s',
                    (str(operation_id),),
                )
                code: Final = (
                    SyncOperationPersistenceFailureCode.STATE_CONFLICT
                    if await exists_cursor.fetchone() is not None
                    else SyncOperationPersistenceFailureCode.OPERATION_NOT_FOUND
                )
                return _failure(code, retryable=False)
        except (ValidationError, ValueError, KeyError, TypeError):
            return _failure(SyncOperationPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _failure(SyncOperationPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def _connect(self) -> AsyncConnection[Mapping[str, object]]:
        row_factory: Final = cast(AsyncRowFactory[Mapping[str, object]], dict_row)
        return await AsyncConnection[Mapping[str, object]].connect(self._database_url, row_factory=row_factory)

    async def _set_search_path(self, connection: AsyncConnection[Mapping[str, object]]) -> None:
        await connection.execute("SELECT set_config('search_path', %s, true)", (self._schema,))


def decode_operation_row(value: object) -> SyncOperation:
    row: Final = _OperationRow.model_validate(value)
    desired: Final = ChannelDesiredState.model_validate(row.desired_payload)
    if desired.schema_version != row.desired_schema_version:
        raise ValueError("stored desired schema versions do not match")
    if desired.channel_id != row.channel_id:
        raise ValueError("stored desired channel does not match operation channel")
    failure: Final = (
        None
        if row.failure_code is None or row.failure_message is None
        else SafeSyncFailure(code=row.failure_code, message=row.failure_message)
    )
    return SyncOperation(
        operation_id=row.operation_id,
        idempotency_key=row.idempotency_key,
        channel_id=row.channel_id,
        action=row.action,
        status=row.status,
        delete_mode=row.delete_mode,
        desired=desired,
        attempt_count=row.attempt_count,
        requires_key=row.requires_key,
        failure=failure,
        created_at=row.created_at,
        updated_at=row.updated_at,
        applied_at=row.applied_at,
    )


def _operation_values(operation: SyncOperation) -> tuple[object, ...]:
    return (
        str(operation.operation_id),
        operation.idempotency_key,
        str(operation.channel_id),
        operation.action.value,
        operation.status.value,
        None if operation.delete_mode is None else operation.delete_mode.value,
        operation.desired.schema_version,
        Jsonb(operation.desired.model_dump(mode="json")),
        operation.attempt_count,
        operation.requires_key,
        None if operation.failure is None else operation.failure.code,
        None if operation.failure is None else operation.failure.message,
        operation.created_at,
        operation.updated_at,
        operation.applied_at,
    )


def _failure(
    code: SyncOperationPersistenceFailureCode,
    retryable: bool,
) -> SyncOperationPersistenceFailure:
    return SyncOperationPersistenceFailure(code=code, retryable=retryable)
