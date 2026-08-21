"""验证统一事件日志的游标和健康、管理事件脱敏解码。"""

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from account_pool.events import (
    EventLogFailure,
    EventLogFailureCode,
    EventLogPage,
    EventQuery,
    EventQueryOutcome,
    PostgresEventLogRepository,
)
from account_pool.events.postgres import decode_event_cursor, decode_event_row, encode_event_cursor
from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import ValidationError

_EVENT_ID: Final = UUID("91000000-0000-0000-0000-000000000001")
_CHANNEL_ID: Final = UUID("91000000-0000-0000-0000-000000000002")
_ENVELOPE_ID: Final = UUID("91000000-0000-0000-0000-000000000003")
_NOW: Final = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class EventRepositoryFixture:
    database_url: str
    schema: str
    repository: PostgresEventLogRepository


@pytest_asyncio.fixture
async def event_repository_fixture() -> AsyncIterator[EventRepositoryFixture]:
    database_url: Final = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    schema: Final = f"account_pool_event_log_test_{uuid4().hex}"
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as connection:
        await connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            await connection.execute("SELECT set_config('search_path', %s, false)", (schema,))
            await connection.execute(
                b"""
                CREATE TABLE "LiteLLM_AccountPoolEvent" (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    occurred_at TIMESTAMPTZ NOT NULL,
                    channel_id TEXT,
                    model_id TEXT,
                    deployment_id TEXT,
                    request_id TEXT,
                    lease_id TEXT,
                    reason_code TEXT,
                    actor_type TEXT,
                    actor_id TEXT,
                    safe_details_schema_version INTEGER NOT NULL DEFAULT 1,
                    safe_details JSONB NOT NULL
                );
                CREATE TABLE "LiteLLM_AccountPoolAuditEvent" (
                    event_id TEXT PRIMARY KEY REFERENCES "LiteLLM_AccountPoolEvent"(event_id),
                    operation_id TEXT,
                    actor_role TEXT NOT NULL,
                    actor_action TEXT NOT NULL,
                    actor_envelope_id TEXT NOT NULL,
                    outcome TEXT NOT NULL
                );
                CREATE TABLE "LiteLLM_AccountPoolHealthEvent" (
                    event_id TEXT PRIMARY KEY REFERENCES "LiteLLM_AccountPoolEvent"(event_id),
                    account_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    transition TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    retry_at TIMESTAMPTZ,
                    probe_trigger TEXT
                );
                """
            )
            yield EventRepositoryFixture(
                database_url=database_url,
                schema=schema,
                repository=PostgresEventLogRepository(database_url, schema=schema),
            )
        finally:
            await connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_management_event_decodes_registered_safe_details_and_cursor() -> None:
    event: Final = decode_event_row(
        {
            **_common_row("channel_create", {"kind": "channel_create", "outcome": {"status": "accepted"}}),
            "operation_id": None,
            "actor_role": "proxy_admin",
            "actor_action": "channel:create",
            "actor_envelope_id": _ENVELOPE_ID,
            "audit_outcome": "accepted",
            **_empty_health_fact(),
        }
    )

    cursor: Final = decode_event_cursor(encode_event_cursor(event))
    assert event.outcome == EventQueryOutcome.ACCEPTED
    assert event.audit is not None and event.audit.actor_action == "channel:create"
    assert event.health is None
    assert cursor is not None and not isinstance(cursor, EventLogFailure)
    assert (cursor.occurred_at, cursor.event_id) == (_NOW, _EVENT_ID)
    assert "credential" not in event.model_dump_json().casefold()


def test_health_event_decodes_registered_safe_details() -> None:
    event: Final = decode_event_row(
        {
            **_common_row(
                "passive_health_result",
                {
                    "kind": "passive_health_result",
                    "outcome": "failed",
                    "transition": "cooldown",
                    "response_status_code": 429,
                    "latency_ms": 25,
                },
            ),
            **_empty_audit_fact(),
            "account_id": "primary",
            "health_source": "passive_request",
            "health_outcome": "failed",
            "health_transition": "cooldown",
            "health_scope": "deployment",
            "health_retry_at": _NOW,
            "health_probe_trigger": None,
        }
    )

    assert event.outcome == EventQueryOutcome.FAILED
    assert event.health is not None and event.health.transition == "cooldown"
    assert event.audit is None


def test_unknown_or_unregistered_safe_details_are_rejected() -> None:
    with pytest.raises(ValueError, match="registered"):
        decode_event_row(
            {
                **_common_row("unknown_event", {"api_key": "must-not-be-returned"}),
                "operation_id": None,
                "actor_role": "proxy_admin",
                "actor_action": "channel:create",
                "actor_envelope_id": _ENVELOPE_ID,
                "audit_outcome": "accepted",
                **_empty_health_fact(),
            }
        )

    with pytest.raises(ValidationError):
        decode_event_row(
            {
                **_common_row(
                    "channel_create",
                    {
                        "kind": "channel_create",
                        "outcome": {"status": "accepted"},
                        "api_key": "must-not-be-returned",
                    },
                ),
                "operation_id": None,
                "actor_role": "proxy_admin",
                "actor_action": "channel:create",
                "actor_envelope_id": _ENVELOPE_ID,
                "audit_outcome": "accepted",
                **_empty_health_fact(),
            }
        )


def test_invalid_cursor_is_a_non_retryable_failure() -> None:
    result: Final = decode_event_cursor("not-base64!")

    assert result == EventLogFailure(code=EventLogFailureCode.INVALID_CURSOR, retryable=False)


async def test_postgres_query_filters_and_paginates_without_duplicate_events(
    event_repository_fixture: EventRepositoryFixture,
) -> None:
    fixture: Final = event_repository_fixture
    async with await psycopg.AsyncConnection.connect(fixture.database_url) as connection, connection.transaction():
        await connection.execute("SELECT set_config('search_path', %s, true)", (fixture.schema,))
        await _insert_management_event(connection, _EVENT_ID, _NOW)
        await _insert_management_event(
            connection,
            UUID("91000000-0000-0000-0000-000000000004"),
            _NOW - timedelta(minutes=1),
        )

    first: Final = await fixture.repository.list_events(
        EventQuery(
            channel_id=_CHANNEL_ID,
            event_type="channel_create",
            outcome=EventQueryOutcome.ACCEPTED,
            limit=1,
        )
    )
    assert isinstance(first, EventLogPage)
    assert tuple(event.event_id for event in first.events) == (_EVENT_ID,)
    assert first.next_cursor is not None

    second: Final = await fixture.repository.list_events(
        EventQuery(
            channel_id=_CHANNEL_ID,
            event_type="channel_create",
            outcome=EventQueryOutcome.ACCEPTED,
            cursor=first.next_cursor,
            limit=1,
        )
    )
    assert isinstance(second, EventLogPage)
    assert tuple(event.event_id for event in second.events) == (
        UUID("91000000-0000-0000-0000-000000000004"),
    )
    assert second.next_cursor is None


def _common_row(event_type: str, safe_details: object) -> dict[str, object]:
    return {
        "event_id": _EVENT_ID,
        "event_type": event_type,
        "occurred_at": _NOW,
        "channel_id": _CHANNEL_ID,
        "model_id": None,
        "deployment_id": None,
        "request_id": "request-1",
        "lease_id": None,
        "reason_code": None,
        "actor_type": "user" if event_type == "channel_create" else "system",
        "actor_id": "admin" if event_type == "channel_create" else "account_pool_gateway",
        "safe_details": safe_details,
    }


def _empty_audit_fact() -> dict[str, object]:
    return {
        "operation_id": None,
        "actor_role": None,
        "actor_action": None,
        "actor_envelope_id": None,
        "audit_outcome": None,
    }


def _empty_health_fact() -> dict[str, object]:
    return {
        "account_id": None,
        "health_source": None,
        "health_outcome": None,
        "health_transition": None,
        "health_scope": None,
        "health_retry_at": None,
        "health_probe_trigger": None,
    }


async def _insert_management_event(
    connection: psycopg.AsyncConnection[tuple[object, ...]],
    event_id: UUID,
    occurred_at: datetime,
) -> None:
    await connection.execute(
        """
        INSERT INTO "LiteLLM_AccountPoolEvent" (
            event_id, event_type, occurred_at, channel_id, request_id,
            actor_type, actor_id, safe_details_schema_version, safe_details
        ) VALUES (%s, 'channel_create', %s, %s, 'request-1', 'user', 'admin', 1, %s)
        """,
        (
            str(event_id),
            occurred_at,
            str(_CHANNEL_ID),
            Jsonb({"kind": "channel_create", "outcome": {"status": "accepted"}}),
        ),
    )
    await connection.execute(
        """
        INSERT INTO "LiteLLM_AccountPoolAuditEvent" (
            event_id, actor_role, actor_action, actor_envelope_id, outcome
        ) VALUES (%s, 'proxy_admin', 'channel:create', %s, 'accepted')
        """,
        (str(event_id), str(_ENVELOPE_ID)),
    )
