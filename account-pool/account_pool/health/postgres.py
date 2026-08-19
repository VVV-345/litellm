"""使用 PostgreSQL 原子持久化脱敏健康事件并维护最近请求、成功和探测时间。"""

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

from account_pool.eligibility import EligibilityScope
from account_pool.health.models import (
    HealthActivity,
    HealthEventDetails,
    HealthEventFact,
    HealthEventRecord,
    HealthEventType,
    HealthObservationOutcome,
    HealthObservationSource,
    HealthPoolEvent,
    HealthProbeTrigger,
    HealthRequestActivity,
    equivalent_health_records,
)
from account_pool.health.repository import (
    HealthActivityLoadResult,
    HealthActivityLoadSuccess,
    HealthActivityWriteResult,
    HealthActivityWriteSuccess,
    HealthEventListResult,
    HealthEventListSuccess,
    HealthLoadResult,
    HealthLoadSuccess,
    HealthPersistenceFailure,
    HealthPersistenceFailureCode,
    HealthWriteResult,
    HealthWriteSuccess,
)
from account_pool.health.settlement import HealthTransitionAction
from account_pool.models import FrozenModel, ModelName

_SCHEMA_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_DETAILS: Final[TypeAdapter[HealthEventDetails]] = TypeAdapter(HealthEventDetails)
_EVENT_COLUMNS: Final = """
event_id, event_type, occurred_at, channel_id, model_id, deployment_id,
request_id, lease_id, reason_code, actor_type, actor_id,
safe_details_schema_version, safe_details
"""
_HEALTH_COLUMNS: Final = """
event_id, account_id, source, outcome, transition, scope, retry_at, probe_trigger
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
    e.safe_details,
    h.event_id AS health_event_id,
    h.account_id,
    h.source,
    h.outcome,
    h.transition,
    h.scope,
    h.retry_at,
    h.probe_trigger
FROM "LiteLLM_AccountPoolEvent" AS e
JOIN "LiteLLM_AccountPoolHealthEvent" AS h ON h.event_id = e.event_id
"""


class _LinkedRow(FrozenModel):
    common_event_id: UUID
    event_type: HealthEventType
    occurred_at: AwareDatetime
    channel_id: UUID | None
    model_id: ModelName
    deployment_id: str
    request_id: str | None
    lease_id: str | None
    reason_code: str | None
    actor_type: Literal["system"]
    actor_id: str
    safe_details: object
    health_event_id: UUID
    account_id: str
    source: HealthObservationSource
    outcome: HealthObservationOutcome
    transition: HealthTransitionAction
    scope: EligibilityScope
    retry_at: AwareDatetime | None
    probe_trigger: HealthProbeTrigger | None


class _ActivityRow(FrozenModel):
    channel_id: UUID | None
    account_id: str
    model_id: ModelName
    deployment_id: str
    last_request_at: AwareDatetime | None
    last_success_at: AwareDatetime | None
    last_failure_at: AwareDatetime | None
    last_probe_at: AwareDatetime | None
    last_probe_success_at: AwareDatetime | None
    last_probe_failure_at: AwareDatetime | None
    updated_at: AwareDatetime


DatabaseRow: TypeAlias = Mapping[str, object]


class PostgresHealthEventRepository:
    def __init__(self, database_url: str, schema: str = "public") -> None:
        if _SCHEMA_PATTERN.fullmatch(schema) is None:
            raise ValueError("schema must be a valid PostgreSQL identifier")
        self._database_url: Final = database_url
        self._schema: Final = schema

    async def append(self, record: HealthEventRecord) -> HealthWriteResult:
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
                    if existing is not None and equivalent_health_records(existing, record):
                        assert existing is not None
                        return HealthWriteSuccess(status="unchanged", record=existing)
                    return _failure(HealthPersistenceFailureCode.CONTENT_CONFLICT, retryable=False)
                await connection.execute(
                    f"""
                    INSERT INTO "LiteLLM_AccountPoolHealthEvent" ({_HEALTH_COLUMNS})
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    _health_values(record.health),
                )
                await _update_activity_from_event(connection, record)
                return HealthWriteSuccess(status="created", record=record)
        except (ValidationError, ValueError, KeyError, TypeError):
            return _failure(HealthPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.IntegrityError:
            return _failure(HealthPersistenceFailureCode.CONTENT_CONFLICT, retryable=False)
        except psycopg.Error:
            return _failure(HealthPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def record_request(self, activity: HealthRequestActivity) -> HealthActivityWriteResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                await connection.execute(
                    """
                    INSERT INTO "LiteLLM_AccountPoolHealthActivity" (
                        account_id, deployment_id, channel_id, model_id, last_request_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (account_id, deployment_id) DO UPDATE SET
                        channel_id = COALESCE(EXCLUDED.channel_id, "LiteLLM_AccountPoolHealthActivity".channel_id),
                        model_id = EXCLUDED.model_id,
                        last_request_at = GREATEST(
                            "LiteLLM_AccountPoolHealthActivity".last_request_at,
                            EXCLUDED.last_request_at
                        ),
                        updated_at = GREATEST("LiteLLM_AccountPoolHealthActivity".updated_at, EXCLUDED.updated_at)
                    """,
                    (
                        activity.account_id,
                        activity.deployment_id,
                        None if activity.channel_id is None else str(activity.channel_id),
                        activity.model_id,
                        activity.observed_at,
                        activity.observed_at,
                    ),
                )
                return HealthActivityWriteSuccess(activity=activity)
        except (ValidationError, ValueError, KeyError, TypeError):
            return _failure(HealthPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _failure(HealthPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def load(self, event_id: UUID) -> HealthLoadResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                record: Final = await _load_linked(connection, event_id)
                if record is None:
                    return _failure(HealthPersistenceFailureCode.EVENT_NOT_FOUND, retryable=False)
                return HealthLoadSuccess(record=record)
        except (ValidationError, ValueError, KeyError, TypeError):
            return _failure(HealthPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _failure(HealthPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def load_activity(self) -> HealthActivityLoadResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                cursor: Final = await connection.execute(
                    """
                    SELECT channel_id, account_id, model_id, deployment_id,
                           last_request_at, last_success_at, last_failure_at,
                           last_probe_at, last_probe_success_at, last_probe_failure_at, updated_at
                    FROM "LiteLLM_AccountPoolHealthActivity"
                    ORDER BY account_id, deployment_id
                    """
                )
                activities: Final = tuple(decode_activity_row(row) for row in await cursor.fetchall())
                return HealthActivityLoadSuccess(activities=activities)
        except (ValidationError, ValueError, KeyError, TypeError):
            return _failure(HealthPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _failure(HealthPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def list_recent(self, channel_id: UUID, limit: int = 50) -> HealthEventListResult:
        if limit < 1 or limit > 200:
            return _failure(HealthPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                cursor: Final = await connection.execute(
                    f"""
                    {_SELECT_LINKED}
                    WHERE e.channel_id = %s
                    ORDER BY e.occurred_at DESC, e.event_id DESC
                    LIMIT %s
                    """,
                    (str(channel_id), limit),
                )
                records: Final = tuple(decode_health_record(row) for row in await cursor.fetchall())
                return HealthEventListSuccess(records=records)
        except (ValidationError, ValueError, KeyError, TypeError):
            return _failure(HealthPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _failure(HealthPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def _connect(self) -> AsyncConnection[DatabaseRow]:
        row_factory: Final = cast(AsyncRowFactory[DatabaseRow], dict_row)
        return await AsyncConnection[DatabaseRow].connect(self._database_url, row_factory=row_factory)

    async def _set_search_path(self, connection: AsyncConnection[DatabaseRow]) -> None:
        await connection.execute("SELECT set_config('search_path', %s, true)", (self._schema,))


def decode_health_record(value: object) -> HealthEventRecord:
    row: Final = _LinkedRow.model_validate(value)
    return HealthEventRecord(
        event=HealthPoolEvent(
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
        health=HealthEventFact(
            event_id=row.health_event_id,
            account_id=row.account_id,
            source=row.source,
            outcome=row.outcome,
            transition=row.transition,
            scope=row.scope,
            retry_at=row.retry_at,
            probe_trigger=row.probe_trigger,
        ),
    )


def decode_activity_row(value: object) -> HealthActivity:
    row: Final = _ActivityRow.model_validate(value)
    return HealthActivity(
        channel_id=row.channel_id,
        account_id=row.account_id,
        model_id=row.model_id,
        deployment_id=row.deployment_id,
        last_request_at=row.last_request_at,
        last_success_at=row.last_success_at,
        last_failure_at=row.last_failure_at,
        last_probe_at=row.last_probe_at,
        last_probe_success_at=row.last_probe_success_at,
        last_probe_failure_at=row.last_probe_failure_at,
        updated_at=row.updated_at,
    )


async def _load_linked(
    connection: AsyncConnection[DatabaseRow],
    event_id: UUID,
) -> HealthEventRecord | None:
    cursor: Final = await connection.execute(
        f"{_SELECT_LINKED} WHERE e.event_id = %s",
        (str(event_id),),
    )
    row: Final = await cursor.fetchone()
    return None if row is None else decode_health_record(row)


async def _update_activity_from_event(
    connection: AsyncConnection[DatabaseRow],
    record: HealthEventRecord,
) -> None:
    event: Final = record.event
    fact: Final = record.health
    passive: Final = fact.source == HealthObservationSource.PASSIVE_REQUEST
    probe: Final = fact.source == HealthObservationSource.ACTIVE_PROBE
    succeeded: Final = fact.outcome == HealthObservationOutcome.SUCCEEDED
    await connection.execute(
        """
        INSERT INTO "LiteLLM_AccountPoolHealthActivity" (
            account_id, deployment_id, channel_id, model_id,
            last_request_at, last_success_at, last_failure_at,
            last_probe_at, last_probe_success_at, last_probe_failure_at, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (account_id, deployment_id) DO UPDATE SET
            channel_id = COALESCE(EXCLUDED.channel_id, "LiteLLM_AccountPoolHealthActivity".channel_id),
            model_id = EXCLUDED.model_id,
            last_request_at = GREATEST(
                "LiteLLM_AccountPoolHealthActivity".last_request_at,
                EXCLUDED.last_request_at
            ),
            last_success_at = GREATEST(
                "LiteLLM_AccountPoolHealthActivity".last_success_at,
                EXCLUDED.last_success_at
            ),
            last_failure_at = GREATEST(
                "LiteLLM_AccountPoolHealthActivity".last_failure_at,
                EXCLUDED.last_failure_at
            ),
            last_probe_at = GREATEST(
                "LiteLLM_AccountPoolHealthActivity".last_probe_at,
                EXCLUDED.last_probe_at
            ),
            last_probe_success_at = GREATEST(
                "LiteLLM_AccountPoolHealthActivity".last_probe_success_at,
                EXCLUDED.last_probe_success_at
            ),
            last_probe_failure_at = GREATEST(
                "LiteLLM_AccountPoolHealthActivity".last_probe_failure_at,
                EXCLUDED.last_probe_failure_at
            ),
            updated_at = GREATEST("LiteLLM_AccountPoolHealthActivity".updated_at, EXCLUDED.updated_at)
        """,
        (
            fact.account_id,
            event.deployment_id,
            None if event.channel_id is None else str(event.channel_id),
            event.model_id,
            event.occurred_at if passive else None,
            event.occurred_at if passive and succeeded else None,
            event.occurred_at if passive and not succeeded else None,
            event.occurred_at if probe else None,
            event.occurred_at if probe and succeeded else None,
            event.occurred_at if probe and not succeeded else None,
            event.occurred_at,
        ),
    )


def _event_values(event: HealthPoolEvent) -> tuple[object, ...]:
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


def _health_values(fact: HealthEventFact) -> tuple[object, ...]:
    return (
        str(fact.event_id),
        fact.account_id,
        fact.source.value,
        fact.outcome.value,
        fact.transition.value,
        fact.scope.value,
        fact.retry_at,
        None if fact.probe_trigger is None else fact.probe_trigger.value,
    )


def _failure(code: HealthPersistenceFailureCode, retryable: bool) -> HealthPersistenceFailure:
    return HealthPersistenceFailure(code=code, retryable=retryable)
