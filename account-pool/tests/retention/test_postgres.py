"""验证 PostgreSQL 归档按月份和事件类型选取，并精确删除关联事实。"""

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from account_pool.retention.models import RetentionBatch, RetentionScope
from account_pool.retention.postgres import PostgresRetentionRepository
from psycopg import sql
from psycopg.types.json import Jsonb

_STANDARD_EVENT_ID: Final = UUID("61000000-0000-0000-0000-000000000001")
_AUDIT_EVENT_ID: Final = UUID("61000000-0000-0000-0000-000000000002")
_CHANNEL_ID: Final = UUID("61000000-0000-0000-0000-000000000003")


@dataclass(frozen=True, slots=True)
class RetentionRepositoryFixture:
    database_url: str
    schema: str
    repository: PostgresRetentionRepository


@pytest_asyncio.fixture
async def retention_repository_fixture() -> AsyncIterator[RetentionRepositoryFixture]:
    database_url: Final = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    schema: Final = f"account_pool_retention_test_{uuid4().hex}"
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
                    event_id TEXT PRIMARY KEY REFERENCES "LiteLLM_AccountPoolEvent"(event_id) ON DELETE RESTRICT,
                    operation_id TEXT,
                    actor_role TEXT NOT NULL,
                    actor_action TEXT NOT NULL,
                    actor_envelope_id TEXT NOT NULL,
                    outcome TEXT NOT NULL
                );
                CREATE TABLE "LiteLLM_AccountPoolHealthEvent" (
                    event_id TEXT PRIMARY KEY REFERENCES "LiteLLM_AccountPoolEvent"(event_id) ON DELETE RESTRICT,
                    account_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    transition TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    retry_at TIMESTAMPTZ,
                    probe_trigger TEXT
                );
                CREATE TABLE "LiteLLM_AccountPoolOperationalEvent" (
                    event_id TEXT PRIMARY KEY REFERENCES "LiteLLM_AccountPoolEvent"(event_id) ON DELETE RESTRICT,
                    source TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    outcome TEXT NOT NULL
                );
                """
            )
            yield RetentionRepositoryFixture(
                database_url=database_url,
                schema=schema,
                repository=PostgresRetentionRepository(database_url, schema=schema),
            )
        finally:
            await connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def test_repository_separates_standard_and_audit_events_and_deletes_exact_ids(
    retention_repository_fixture: RetentionRepositoryFixture,
) -> None:
    fixture: Final = retention_repository_fixture
    async with await psycopg.AsyncConnection.connect(fixture.database_url) as connection, connection.transaction():
        await connection.execute("SELECT set_config('search_path', %s, true)", (fixture.schema,))
        await _insert_standard_event(connection)
        await _insert_audit_event(connection)

    standard: Final = await fixture.repository.load_oldest_month(
        scope=RetentionScope.STANDARD,
        before=datetime(2026, 3, 1, tzinfo=UTC),
        limit=100,
    )
    assert isinstance(standard, RetentionBatch)
    assert tuple(event.event_id for event in standard.events) == (_STANDARD_EVENT_ID,)
    assert await fixture.repository.delete_archived((_STANDARD_EVENT_ID,)) == 1

    audit: Final = await fixture.repository.load_oldest_month(
        scope=RetentionScope.AUDIT,
        before=datetime(2026, 3, 1, tzinfo=UTC),
        limit=100,
    )
    assert isinstance(audit, RetentionBatch)
    assert tuple(event.event_id for event in audit.events) == (_AUDIT_EVENT_ID,)


async def _insert_standard_event(connection: psycopg.AsyncConnection[tuple[object, ...]]) -> None:
    task_id: Final = UUID("62000000-0000-0000-0000-000000000001")
    await connection.execute(
        """
        INSERT INTO "LiteLLM_AccountPoolEvent" (
            event_id, event_type, occurred_at, channel_id, actor_type, actor_id, safe_details
        ) VALUES (%s, 'parser_task_completed', %s, %s, 'system', 'account_pool_parser_task', %s)
        """,
        (
            str(_STANDARD_EVENT_ID),
            datetime(2026, 1, 2, tzinfo=UTC),
            str(_CHANNEL_ID),
            Jsonb(
                {
                    "kind": "parser_task_completed",
                    "task_id": str(task_id),
                    "parser_run_id": "62000000-0000-0000-0000-000000000002",
                    "provider_id": "openai_compatible",
                }
            ),
        ),
    )
    await connection.execute(
        """
        INSERT INTO "LiteLLM_AccountPoolOperationalEvent" (event_id, source, operation_id, outcome)
        VALUES (%s, 'parser_task', %s, 'succeeded')
        """,
        (str(_STANDARD_EVENT_ID), str(task_id)),
    )


async def _insert_audit_event(connection: psycopg.AsyncConnection[tuple[object, ...]]) -> None:
    await connection.execute(
        """
        INSERT INTO "LiteLLM_AccountPoolEvent" (
            event_id, event_type, occurred_at, channel_id, actor_type, actor_id, safe_details
        ) VALUES (%s, 'channel_create', %s, %s, 'user', 'admin', %s)
        """,
        (
            str(_AUDIT_EVENT_ID),
            datetime(2026, 1, 3, tzinfo=UTC),
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
        (str(_AUDIT_EVENT_ID), "62000000-0000-0000-0000-000000000003"),
    )
