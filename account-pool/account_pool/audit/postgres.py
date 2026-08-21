"""使用 PostgreSQL 原子追加公共事件信封及其管理审计事实。"""

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

from account_pool.audit.models import (
    AuditOutcome,
    ManagementAuditDetails,
    ManagementAuditFact,
    ManagementAuditRecord,
    ManagementEventType,
    PoolEvent,
)
from account_pool.audit.repository import (
    AuditLoadResult,
    AuditLoadSuccess,
    AuditPersistenceFailure,
    AuditPersistenceFailureCode,
    AuditWriteResult,
    AuditWriteSuccess,
)
from account_pool.auth.actor import ActorAction
from account_pool.models import FrozenModel, ModelName

_SCHEMA_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_DETAILS: Final[TypeAdapter[ManagementAuditDetails]] = TypeAdapter(ManagementAuditDetails)
_EVENT_COLUMNS: Final = """
event_id, event_type, occurred_at, channel_id, model_id, deployment_id,
request_id, lease_id, reason_code, actor_type, actor_id,
safe_details_schema_version, safe_details
"""
_AUDIT_COLUMNS: Final = """
event_id, operation_id, actor_role, actor_action, actor_envelope_id, outcome
"""
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
    e.safe_details_schema_version,
    e.safe_details,
    a.event_id AS audit_event_id,
    a.operation_id,
    a.actor_role,
    a.actor_action,
    a.actor_envelope_id,
    a.outcome
FROM "LiteLLM_AccountPoolEvent" AS e
JOIN "LiteLLM_AccountPoolAuditEvent" AS a ON a.event_id = e.event_id
WHERE e.event_id = %s
"""


class _EventRow(FrozenModel):
    event_id: UUID
    event_type: ManagementEventType
    occurred_at: AwareDatetime
    channel_id: UUID | None
    model_id: ModelName | None
    deployment_id: str | None
    request_id: str | None
    lease_id: str | None
    reason_code: str | None
    actor_type: Literal["user", "system"]
    actor_id: str
    safe_details_schema_version: Literal[1]
    safe_details: object


class _AuditRow(FrozenModel):
    event_id: UUID
    operation_id: UUID | None
    actor_role: Literal["proxy_admin", "system"]
    actor_action: ActorAction
    actor_envelope_id: UUID
    outcome: AuditOutcome


class _LinkedRow(FrozenModel):
    common_event_id: UUID
    event_type: ManagementEventType
    occurred_at: AwareDatetime
    channel_id: UUID | None
    model_id: ModelName | None
    deployment_id: str | None
    request_id: str | None
    lease_id: str | None
    reason_code: str | None
    actor_type: Literal["user", "system"]
    actor_id: str
    safe_details: object
    audit_event_id: UUID
    operation_id: UUID | None
    actor_role: Literal["proxy_admin", "system"]
    actor_action: ActorAction
    actor_envelope_id: UUID
    outcome: AuditOutcome


DatabaseRow: TypeAlias = Mapping[str, object]


class PostgresManagementAuditRepository:
    def __init__(self, database_url: str, schema: str = "public") -> None:
        if _SCHEMA_PATTERN.fullmatch(schema) is None:
            raise ValueError("schema must be a valid PostgreSQL identifier")
        self._database_url: Final = database_url
        self._schema: Final = schema

    async def append(self, record: ManagementAuditRecord) -> AuditWriteResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                event_cursor: Final = await connection.execute(
                    f"""
                    INSERT INTO "LiteLLM_AccountPoolEvent" ({_EVENT_COLUMNS})
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (event_id) DO NOTHING
                    RETURNING event_id
                    """,
                    _event_values(record.event),
                )
                if await event_cursor.fetchone() is None:
                    existing: Final = await _load_linked(connection, record.event.event_id)
                    if existing == record:
                        assert existing is not None
                        return AuditWriteSuccess(status="unchanged", record=existing)
                    return _failure(AuditPersistenceFailureCode.CONTENT_CONFLICT, retryable=False)
                await connection.execute(
                    f"""
                    INSERT INTO "LiteLLM_AccountPoolAuditEvent" ({_AUDIT_COLUMNS})
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    _audit_values(record.audit),
                )
                return AuditWriteSuccess(status="created", record=record)
        except (ValidationError, ValueError, KeyError, TypeError):
            return _failure(AuditPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.IntegrityError:
            return _failure(AuditPersistenceFailureCode.CONTENT_CONFLICT, retryable=False)
        except psycopg.Error:
            return _failure(AuditPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def load(self, event_id: UUID) -> AuditLoadResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                record: Final = await _load_linked(connection, event_id)
                if record is None:
                    return _failure(AuditPersistenceFailureCode.EVENT_NOT_FOUND, retryable=False)
                return AuditLoadSuccess(record=record)
        except (ValidationError, ValueError, KeyError, TypeError):
            return _failure(AuditPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _failure(AuditPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def _connect(self) -> AsyncConnection[DatabaseRow]:
        row_factory: Final = cast(AsyncRowFactory[DatabaseRow], dict_row)
        return await AsyncConnection[DatabaseRow].connect(self._database_url, row_factory=row_factory)

    async def _set_search_path(self, connection: AsyncConnection[DatabaseRow]) -> None:
        await connection.execute("SELECT set_config('search_path', %s, true)", (self._schema,))


def decode_management_audit_rows(event_value: object, audit_value: object) -> ManagementAuditRecord:
    event_row: Final = _EventRow.model_validate(event_value)
    audit_row: Final = _AuditRow.model_validate(audit_value)
    return ManagementAuditRecord(
        event=PoolEvent(
            event_id=event_row.event_id,
            event_type=event_row.event_type,
            occurred_at=event_row.occurred_at,
            channel_id=event_row.channel_id,
            model_id=event_row.model_id,
            deployment_id=event_row.deployment_id,
            request_id=event_row.request_id,
            lease_id=event_row.lease_id,
            reason_code=event_row.reason_code,
            actor_type=event_row.actor_type,
            actor_id=event_row.actor_id,
            safe_details=_SAFE_DETAILS.validate_python(event_row.safe_details),
        ),
        audit=ManagementAuditFact(
            event_id=audit_row.event_id,
            operation_id=audit_row.operation_id,
            actor_role=audit_row.actor_role,
            actor_action=audit_row.actor_action,
            actor_envelope_id=audit_row.actor_envelope_id,
            outcome=audit_row.outcome,
        ),
    )


async def _load_linked(
    connection: AsyncConnection[DatabaseRow],
    event_id: UUID,
) -> ManagementAuditRecord | None:
    cursor: Final = await connection.execute(_SELECT_LINKED, (str(event_id),))
    value: Final = await cursor.fetchone()
    if value is None:
        return None
    row: Final = _LinkedRow.model_validate(value)
    return decode_management_audit_rows(
        {
            "event_id": row.common_event_id,
            "event_type": row.event_type,
            "occurred_at": row.occurred_at,
            "channel_id": row.channel_id,
            "model_id": row.model_id,
            "deployment_id": row.deployment_id,
            "request_id": row.request_id,
            "lease_id": row.lease_id,
            "reason_code": row.reason_code,
            "actor_type": row.actor_type,
            "actor_id": row.actor_id,
            "safe_details_schema_version": 1,
            "safe_details": row.safe_details,
        },
        {
            "event_id": row.audit_event_id,
            "operation_id": row.operation_id,
            "actor_role": row.actor_role,
            "actor_action": row.actor_action,
            "actor_envelope_id": row.actor_envelope_id,
            "outcome": row.outcome,
        },
    )


def _event_values(event: PoolEvent) -> tuple[object, ...]:
    return (
        str(event.event_id),
        event.event_type.value,
        event.occurred_at,
        None if event.channel_id is None else str(event.channel_id),
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


def _audit_values(audit: ManagementAuditFact) -> tuple[object, ...]:
    return (
        str(audit.event_id),
        None if audit.operation_id is None else str(audit.operation_id),
        audit.actor_role,
        audit.actor_action.value,
        str(audit.actor_envelope_id),
        audit.outcome.value,
    )


def _failure(code: AuditPersistenceFailureCode, retryable: bool) -> AuditPersistenceFailure:
    return AuditPersistenceFailure(code=code, retryable=retryable)
