"""从 PostgreSQL 公共事件信封分页查询已注册且脱敏的健康与管理事件。"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Mapping
from typing import Final, Literal, TypeAlias, cast
from uuid import UUID

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import AsyncRowFactory, dict_row
from pydantic import AwareDatetime, JsonValue, TypeAdapter, ValidationError, model_validator

from account_pool.audit.models import AuditOutcome, ManagementAuditDetails, ManagementEventType
from account_pool.auth.actor import ActorAction
from account_pool.eligibility import EligibilityScope
from account_pool.events.models import (
    EventAuditSummary,
    EventHealthSummary,
    EventLogEntry,
    EventLogFailure,
    EventLogFailureCode,
    EventLogPage,
    EventLogResult,
    EventOperationalSummary,
    EventQuery,
    EventQueryOutcome,
)
from account_pool.health.models import (
    HealthEventDetails,
    HealthEventType,
    HealthObservationOutcome,
    HealthObservationSource,
    HealthProbeTrigger,
)
from account_pool.health.settlement import HealthTransitionAction
from account_pool.models import FrozenModel, ModelName
from account_pool.operational.models import (
    OperationalEventDetails,
    OperationalEventOutcome,
    OperationalEventSource,
    OperationalEventType,
)

_SCHEMA_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MANAGEMENT_DETAILS: Final[TypeAdapter[ManagementAuditDetails]] = TypeAdapter(ManagementAuditDetails)
_HEALTH_DETAILS: Final[TypeAdapter[HealthEventDetails]] = TypeAdapter(HealthEventDetails)
_OPERATIONAL_DETAILS: Final[TypeAdapter[OperationalEventDetails]] = TypeAdapter(OperationalEventDetails)
_EVENT_QUERY: Final = """
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
WHERE (%s::timestamptz IS NULL OR e.occurred_at >= %s::timestamptz)
  AND (%s::timestamptz IS NULL OR e.occurred_at <= %s::timestamptz)
  AND (%s::text IS NULL OR e.channel_id = %s::text)
  AND (%s::text IS NULL OR e.model_id = %s::text)
  AND (%s::text IS NULL OR e.event_type = %s::text)
  AND (%s::text IS NULL OR h.outcome = %s::text)
  AND (%s::text IS NULL OR h.transition = %s::text)
  AND (%s::text IS NULL OR e.reason_code = %s::text)
  AND (%s::text IS NULL OR e.request_id = %s::text)
  AND (%s::text IS NULL OR COALESCE(a.outcome, h.outcome, o.outcome) = %s::text)
  AND (
      %s::timestamptz IS NULL
      OR (e.occurred_at, e.event_id) < (%s::timestamptz, %s::text)
  )
ORDER BY e.occurred_at DESC, e.event_id DESC
LIMIT %s
"""


class _Cursor(FrozenModel):
    occurred_at: AwareDatetime
    event_id: UUID


class _EventRow(FrozenModel):
    event_id: UUID
    event_type: str
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
    operation_id: UUID | None
    actor_role: Literal["proxy_admin", "system"] | None
    actor_action: ActorAction | None
    actor_envelope_id: UUID | None
    audit_outcome: AuditOutcome | None
    account_id: str | None
    health_source: HealthObservationSource | None
    health_outcome: HealthObservationOutcome | None
    health_transition: HealthTransitionAction | None
    health_scope: EligibilityScope | None
    health_retry_at: AwareDatetime | None
    health_probe_trigger: HealthProbeTrigger | None
    operational_source: OperationalEventSource | None
    operational_operation_id: UUID | None
    operational_outcome: OperationalEventOutcome | None

    @model_validator(mode="after")
    def validate_linked_fact(self) -> _EventRow:
        audit_values: Final = (self.actor_role, self.actor_action, self.actor_envelope_id, self.audit_outcome)
        health_values: Final = (
            self.account_id,
            self.health_source,
            self.health_outcome,
            self.health_transition,
            self.health_scope,
        )
        operational_values: Final = (
            self.operational_source,
            self.operational_operation_id,
            self.operational_outcome,
        )
        audit_present: Final = _complete_fact(audit_values)
        health_present: Final = _complete_fact(health_values)
        operational_present: Final = _complete_fact(operational_values)
        if sum((audit_present, health_present, operational_present)) != 1:
            raise ValueError("event row requires exactly one complete linked fact")
        return self


DatabaseRow: TypeAlias = Mapping[str, object]


class PostgresEventLogRepository:
    def __init__(self, database_url: str, schema: str = "public") -> None:
        if _SCHEMA_PATTERN.fullmatch(schema) is None:
            raise ValueError("schema must be a valid PostgreSQL identifier")
        self._database_url: Final = database_url
        self._schema: Final = schema

    async def list_events(self, query: EventQuery) -> EventLogResult:
        cursor: Final = decode_event_cursor(query.cursor)
        if isinstance(cursor, EventLogFailure):
            return cursor
        try:
            async with await self._connect() as connection, connection.transaction():
                await connection.execute("SELECT set_config('search_path', %s, true)", (self._schema,))
                result_cursor: Final = await connection.execute(_EVENT_QUERY, _query_parameters(query, cursor))
                rows: Final = tuple(decode_event_row(row) for row in await result_cursor.fetchall())
                page: Final = rows[: query.limit]
                next_cursor: Final = encode_event_cursor(page[-1]) if len(rows) > query.limit else None
                return EventLogPage(events=page, next_cursor=next_cursor)
        except (ValidationError, ValueError, KeyError, TypeError):
            return EventLogFailure(code=EventLogFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return EventLogFailure(code=EventLogFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def _connect(self) -> AsyncConnection[DatabaseRow]:
        row_factory: Final = cast(AsyncRowFactory[DatabaseRow], dict_row)
        return await AsyncConnection[DatabaseRow].connect(self._database_url, row_factory=row_factory)


def encode_event_cursor(event: EventLogEntry) -> str:
    payload: Final = _Cursor(occurred_at=event.occurred_at, event_id=event.event_id).model_dump_json().encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_event_cursor(value: str | None) -> _Cursor | EventLogFailure | None:
    if value is None:
        return None
    try:
        padding: Final = "=" * (-len(value) % 4)
        payload: Final = base64.b64decode(f"{value}{padding}", altchars=b"-_", validate=True)
        return _Cursor.model_validate_json(payload)
    except (binascii.Error, UnicodeEncodeError, ValidationError, ValueError):
        return EventLogFailure(code=EventLogFailureCode.INVALID_CURSOR, retryable=False)


def _query_parameters(query: EventQuery, cursor: _Cursor | None) -> tuple[object, ...]:
    channel_id: Final = None if query.channel_id is None else str(query.channel_id)
    health_outcome: Final = None if query.health_outcome is None else query.health_outcome.value
    health_transition: Final = None if query.health_transition is None else query.health_transition.value
    outcome: Final = None if query.outcome is None else query.outcome.value
    cursor_time: Final = None if cursor is None else cursor.occurred_at
    cursor_id: Final = None if cursor is None else str(cursor.event_id)
    return (
        query.occurred_after,
        query.occurred_after,
        query.occurred_before,
        query.occurred_before,
        channel_id,
        channel_id,
        query.model_id,
        query.model_id,
        query.event_type,
        query.event_type,
        health_outcome,
        health_outcome,
        health_transition,
        health_transition,
        query.reason_code,
        query.reason_code,
        query.request_id,
        query.request_id,
        outcome,
        outcome,
        cursor_time,
        cursor_time,
        cursor_id,
        query.limit + 1,
    )


def decode_event_row(value: object) -> EventLogEntry:
    row: Final = _EventRow.model_validate(value)
    audit: Final = _audit_summary(row)
    health: Final = _health_summary(row)
    operational: Final = _operational_summary(row)
    outcome: Final = (
        audit.outcome.value
        if audit is not None
        else health.outcome.value
        if health is not None
        else operational.outcome
        if operational is not None
        else None
    )
    if outcome is None:
        raise ValueError("linked event outcome is missing")
    return EventLogEntry(
        event_id=row.event_id,
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
        outcome=EventQueryOutcome(outcome),
        safe_details=_safe_details(row.event_type, row.safe_details),
        audit=audit,
        health=health,
        operational=operational,
    )


def _safe_details(event_type: str, value: object) -> JsonValue:
    if event_type in frozenset(item.value for item in ManagementEventType):
        return cast(JsonValue, _MANAGEMENT_DETAILS.validate_python(value).model_dump(mode="json"))
    if event_type in frozenset(item.value for item in HealthEventType):
        return cast(JsonValue, _HEALTH_DETAILS.validate_python(value).model_dump(mode="json"))
    if event_type in frozenset(item.value for item in OperationalEventType):
        return cast(JsonValue, _OPERATIONAL_DETAILS.validate_python(value).model_dump(mode="json"))
    raise ValueError("event type has no registered safe details model")


def _audit_summary(row: _EventRow) -> EventAuditSummary | None:
    if row.actor_role is None:
        return None
    if row.actor_action is None or row.actor_envelope_id is None or row.audit_outcome is None:
        raise ValueError("audit fact is incomplete")
    return EventAuditSummary(
        operation_id=row.operation_id,
        actor_role=row.actor_role,
        actor_action=row.actor_action,
        actor_envelope_id=row.actor_envelope_id,
        outcome=row.audit_outcome,
    )


def _health_summary(row: _EventRow) -> EventHealthSummary | None:
    if row.account_id is None:
        return None
    if (
        row.health_source is None
        or row.health_outcome is None
        or row.health_transition is None
        or row.health_scope is None
    ):
        raise ValueError("health fact is incomplete")
    return EventHealthSummary(
        account_id=row.account_id,
        source=row.health_source,
        outcome=row.health_outcome,
        transition=row.health_transition,
        scope=row.health_scope,
        retry_at=row.health_retry_at,
        probe_trigger=row.health_probe_trigger,
    )


def _operational_summary(row: _EventRow) -> EventOperationalSummary | None:
    if row.operational_source is None:
        return None
    if row.operational_operation_id is None or row.operational_outcome is None:
        raise ValueError("operational fact is incomplete")
    return EventOperationalSummary(
        source=row.operational_source.value,
        operation_id=row.operational_operation_id,
        outcome=row.operational_outcome.value,
    )


def _complete_fact(values: tuple[object | None, ...]) -> bool:
    present: Final = tuple(value is not None for value in values)
    if any(present) and not all(present):
        raise ValueError("linked event fact is incomplete")
    return all(present)
