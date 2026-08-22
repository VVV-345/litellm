"""使用 PostgreSQL 原子持久化公共事件信封和脱敏系统运行事实。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final, Literal, TypeAlias, cast
from uuid import UUID

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import AsyncRowFactory, dict_row
from psycopg.types.json import Jsonb
from pydantic import AwareDatetime, TypeAdapter, ValidationError

from account_pool.models import FrozenModel, ModelName
from account_pool.operational.models import (
    OperationalEventDetails,
    OperationalEventFact,
    OperationalEventOutcome,
    OperationalEventRecord,
    OperationalEventSource,
    OperationalEventType,
    OperationalPoolEvent,
)
from account_pool.operational.repository import (
    OperationalPersistenceFailure,
    OperationalPersistenceFailureCode,
    OperationalWriteResult,
    OperationalWriteSuccess,
)

_SCHEMA_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_DETAILS: Final[TypeAdapter[OperationalEventDetails]] = TypeAdapter(OperationalEventDetails)
_EVENT_COLUMNS: Final = """
event_id, event_type, occurred_at, channel_id, model_id, deployment_id,
request_id, lease_id, reason_code, actor_type, actor_id,
safe_details_schema_version, safe_details
"""
_OPERATIONAL_COLUMNS: Final = "event_id, source, operation_id, outcome"
_SELECT_LINKED: Final = """
SELECT
    e.event_id AS common_event_id,
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
    e.safe_details,
    o.event_id AS operational_event_id,
    o.source,
    o.operation_id,
    o.outcome
FROM "LiteLLM_AccountPoolEvent" AS e
JOIN "LiteLLM_AccountPoolOperationalEvent" AS o ON o.event_id = e.event_id
"""


class _LinkedRow(FrozenModel):
    common_event_id: UUID
    event_type: OperationalEventType
    occurred_at: AwareDatetime
    channel_id: UUID
    model_id: ModelName | None
    deployment_id: str | None
    request_id: str | None
    lease_id: str | None
    reason_code: str | None
    actor_type: Literal["system"]
    actor_id: Literal[
        "account_pool_parser_task",
        "account_pool_parser_snapshot",
        "account_pool_reconciler",
        "account_pool_scheduler",
        "account_pool_state_store",
        "account_pool_lease_reaper",
        "account_pool_eligibility",
        "account_pool_public_metadata",
    ]
    safe_details: object
    operational_event_id: UUID
    source: OperationalEventSource
    operation_id: UUID
    outcome: OperationalEventOutcome


DatabaseRow: TypeAlias = Mapping[str, object]


class PostgresOperationalEventRepository:
    def __init__(self, database_url: str, schema: str = "public") -> None:
        if _SCHEMA_PATTERN.fullmatch(schema) is None:
            raise ValueError("schema must be a valid PostgreSQL identifier")
        self._database_url: Final = database_url
        self._schema: Final = schema

    async def append(self, record: OperationalEventRecord) -> OperationalWriteResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await connection.execute("SELECT set_config('search_path', %s, true)", (self._schema,))
                cursor: Final = await connection.execute(
                    f"""
                    INSERT INTO "LiteLLM_AccountPoolEvent" ({_EVENT_COLUMNS})
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (event_id) DO NOTHING
                    RETURNING event_id
                    """,
                    _event_values(record.event),
                )
                if await cursor.fetchone() is None:
                    existing: Final = await _load_linked(connection, record.event.event_id)
                    if existing == record:
                        return OperationalWriteSuccess(status="unchanged", record=record)
                    return _failure(OperationalPersistenceFailureCode.CONTENT_CONFLICT, retryable=False)
                await connection.execute(
                    f"""
                    INSERT INTO "LiteLLM_AccountPoolOperationalEvent" ({_OPERATIONAL_COLUMNS})
                    VALUES (%s, %s, %s, %s)
                    """,
                    _operational_values(record.operational),
                )
                return OperationalWriteSuccess(status="created", record=record)
        except (ValidationError, ValueError, KeyError, TypeError):
            return _failure(OperationalPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.IntegrityError:
            return _failure(OperationalPersistenceFailureCode.CONTENT_CONFLICT, retryable=False)
        except psycopg.Error:
            return _failure(OperationalPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def _connect(self) -> AsyncConnection[DatabaseRow]:
        row_factory: Final = cast(AsyncRowFactory[DatabaseRow], dict_row)
        return await AsyncConnection[DatabaseRow].connect(self._database_url, row_factory=row_factory)


async def _load_linked(connection: AsyncConnection[DatabaseRow], event_id: UUID) -> OperationalEventRecord | None:
    cursor: Final = await connection.execute(
        f"{_SELECT_LINKED} WHERE e.event_id = %s",
        (str(event_id),),
    )
    row: Final = await cursor.fetchone()
    return None if row is None else decode_operational_record(row)


def decode_operational_record(value: object) -> OperationalEventRecord:
    row: Final = _LinkedRow.model_validate(value)
    if row.common_event_id != row.operational_event_id:
        raise ValueError("common event and operational fact must share an event ID")
    return OperationalEventRecord(
        event=OperationalPoolEvent(
            event_id=row.common_event_id,
            event_type=row.event_type,
            occurred_at=row.occurred_at,
            channel_id=row.channel_id,
            model_id=row.model_id,
            deployment_id=row.deployment_id,
            request_id=row.request_id,
            lease_id=row.lease_id,
            reason_code=row.reason_code,
            actor_type=row.actor_type,
            actor_id=row.actor_id,
            safe_details=_SAFE_DETAILS.validate_python(row.safe_details),
        ),
        operational=OperationalEventFact(
            event_id=row.operational_event_id,
            source=row.source,
            operation_id=row.operation_id,
            outcome=row.outcome,
        ),
    )


def _event_values(event: OperationalPoolEvent) -> tuple[object, ...]:
    return (
        str(event.event_id),
        event.event_type.value,
        event.occurred_at,
        str(event.channel_id),
        event.model_id,
        event.deployment_id,
        event.request_id,
        event.lease_id,
        event.reason_code,
        event.actor_type,
        event.actor_id,
        1,
        Jsonb(event.safe_details.model_dump(mode="json")),
    )


def _operational_values(fact: OperationalEventFact) -> tuple[object, ...]:
    return (
        str(fact.event_id),
        fact.source.value,
        str(fact.operation_id),
        fact.outcome.value,
    )


def _failure(code: OperationalPersistenceFailureCode, retryable: bool) -> OperationalPersistenceFailure:
    return OperationalPersistenceFailure(code=code, retryable=retryable)
