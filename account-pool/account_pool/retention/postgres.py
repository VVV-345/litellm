"""从 PostgreSQL 按最早月份读取归档事件，并按已验证 ID 事务删除。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Final, TypeAlias, cast
from uuid import UUID

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import AsyncRowFactory, dict_row
from pydantic import AwareDatetime, StrictInt, TypeAdapter, ValidationError

from account_pool.events.models import EventLogEntry
from account_pool.events.postgres import decode_event_row
from account_pool.retention.models import (
    ArchiveWindow,
    RetentionBatch,
    RetentionFailure,
    RetentionFailureCode,
    RetentionScope,
)

_SCHEMA_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SCHEMA_VERSION: Final[TypeAdapter[StrictInt]] = TypeAdapter(StrictInt)
_EARLIEST_QUERY: Final = """
SELECT MIN(e.occurred_at) AS occurred_at
FROM "LiteLLM_AccountPoolEvent" AS e
LEFT JOIN "LiteLLM_AccountPoolAuditEvent" AS a ON a.event_id = e.event_id
WHERE e.occurred_at < %s
  AND (
      (%s = 'audit' AND a.event_id IS NOT NULL)
      OR (%s = 'standard' AND a.event_id IS NULL)
  )
"""
_BATCH_QUERY: Final = """
SELECT
    e.event_id,
    e.event_type,
    e.occurred_at,
    e.channel_id,
    e.model_id,
    e.deployment_id,
    e.request_id,
    e.lease_id,
    e.reason_code,
    e.actor_type,
    e.actor_id,
    e.safe_details_schema_version,
    e.safe_details,
    a.operation_id,
    a.actor_role,
    a.actor_action,
    a.actor_envelope_id,
    a.outcome AS audit_outcome,
    h.account_id,
    h.source AS health_source,
    h.outcome AS health_outcome,
    h.transition AS health_transition,
    h.scope AS health_scope,
    h.retry_at AS health_retry_at,
    h.probe_trigger AS health_probe_trigger,
    o.source AS operational_source,
    o.operation_id AS operational_operation_id,
    o.outcome AS operational_outcome
FROM "LiteLLM_AccountPoolEvent" AS e
LEFT JOIN "LiteLLM_AccountPoolAuditEvent" AS a ON a.event_id = e.event_id
LEFT JOIN "LiteLLM_AccountPoolHealthEvent" AS h ON h.event_id = e.event_id
LEFT JOIN "LiteLLM_AccountPoolOperationalEvent" AS o ON o.event_id = e.event_id
WHERE e.occurred_at >= %s
  AND e.occurred_at < %s
  AND (
      (%s = 'audit' AND a.event_id IS NOT NULL)
      OR (%s = 'standard' AND a.event_id IS NULL)
  )
ORDER BY e.occurred_at ASC, e.event_id ASC
LIMIT %s
"""
_DELETE_FACT_QUERIES: Final = (
    'DELETE FROM "LiteLLM_AccountPoolAuditEvent" WHERE event_id = ANY(%s::text[])',
    'DELETE FROM "LiteLLM_AccountPoolHealthEvent" WHERE event_id = ANY(%s::text[])',
    'DELETE FROM "LiteLLM_AccountPoolOperationalEvent" WHERE event_id = ANY(%s::text[])',
)

DatabaseRow: TypeAlias = Mapping[str, object]


class PostgresRetentionRepository:
    def __init__(self, database_url: str, schema: str = "public") -> None:
        if _SCHEMA_PATTERN.fullmatch(schema) is None:
            raise ValueError("schema must be a valid PostgreSQL identifier")
        self._database_url: Final = database_url
        self._schema: Final = schema

    async def load_oldest_month(
        self,
        *,
        scope: RetentionScope,
        before: AwareDatetime,
        limit: int,
    ) -> RetentionBatch | RetentionFailure | None:
        if limit < 1:
            raise ValueError("retention batch limit must be positive")
        try:
            async with await self._connect() as connection, connection.transaction():
                await connection.execute("SELECT set_config('search_path', %s, true)", (self._schema,))
                earliest_cursor: Final = await connection.execute(
                    _EARLIEST_QUERY,
                    (before, scope.value, scope.value),
                )
                earliest_row: Final = await earliest_cursor.fetchone()
                earliest: Final = None if earliest_row is None else earliest_row.get("occurred_at")
                if earliest is None:
                    return None
                started_at, ended_at = _month_bounds(earliest)
                batch_cursor: Final = await connection.execute(
                    _BATCH_QUERY,
                    (started_at, ended_at, scope.value, scope.value, limit),
                )
                events: Final = tuple(_decode_retention_row(row) for row in await batch_cursor.fetchall())
                if not events:
                    return None
                return RetentionBatch(
                    scope=scope,
                    window=ArchiveWindow(started_at=started_at, ended_at=ended_at),
                    events=events,
                )
        except (ValidationError, ValueError, KeyError, TypeError):
            return RetentionFailure(code=RetentionFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return RetentionFailure(code=RetentionFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def delete_archived(self, event_ids: tuple[UUID, ...]) -> int | RetentionFailure:
        if not event_ids:
            return 0
        identifiers: Final = tuple(str(event_id) for event_id in event_ids)
        try:
            async with await self._connect() as connection, connection.transaction():
                await connection.execute("SELECT set_config('search_path', %s, true)", (self._schema,))
                for statement in _DELETE_FACT_QUERIES:
                    await connection.execute(statement, (identifiers,))
                deleted_cursor: Final = await connection.execute(
                    'DELETE FROM "LiteLLM_AccountPoolEvent" WHERE event_id = ANY(%s::text[]) RETURNING event_id',
                    (identifiers,),
                )
                return len(await deleted_cursor.fetchall())
        except psycopg.Error:
            return RetentionFailure(code=RetentionFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def _connect(self) -> AsyncConnection[DatabaseRow]:
        row_factory: Final = cast(AsyncRowFactory[DatabaseRow], dict_row)
        return await AsyncConnection[DatabaseRow].connect(self._database_url, row_factory=row_factory)


def _month_bounds(value: object) -> tuple[AwareDatetime, AwareDatetime]:
    occurred_at: Final[datetime] = cast(datetime, TypeAdapter(AwareDatetime).validate_python(value))
    started_at: Final = occurred_at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    ended_at: Final = (
        started_at.replace(year=started_at.year + 1, month=1)
        if started_at.month == 12
        else started_at.replace(month=started_at.month + 1)
    )
    return started_at, ended_at


def _decode_retention_row(row: DatabaseRow) -> EventLogEntry:
    schema_version: Final = _SCHEMA_VERSION.validate_python(row.get("safe_details_schema_version"))
    if schema_version != 1:
        raise ValueError("unsupported safe details schema version")
    event_row: Final = {key: value for key, value in row.items() if key != "safe_details_schema_version"}
    return decode_event_row(event_row)
