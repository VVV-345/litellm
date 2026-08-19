"""验证 PostgreSQL 管理审计仓储的原子追加、幂等和安全行解码。"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from account_pool.audit.models import (
    AuditOutcome,
    ChannelCreateDetails,
    SafeAuditOutcome,
    build_management_audit_record,
)
from account_pool.audit.postgres import PostgresManagementAuditRepository, decode_management_audit_rows
from account_pool.audit.repository import (
    AuditLoadSuccess,
    AuditPersistenceFailure,
    AuditPersistenceFailureCode,
    AuditWriteSuccess,
)
from account_pool.auth.actor import ActorAction, ActorContext
from psycopg import sql
from pydantic import ValidationError

_EVENT_ID: Final = UUID("40000000-0000-0000-0000-000000000001")
_OPERATION_ID: Final = UUID("40000000-0000-0000-0000-000000000002")
_CHANNEL_ID: Final = UUID("40000000-0000-0000-0000-000000000003")
_NOW: Final = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class AuditRepositoryFixture:
    database_url: str
    schema: str
    repository: PostgresManagementAuditRepository


def _record(event_id: UUID = _EVENT_ID, operation_id: UUID = _OPERATION_ID):
    return build_management_audit_record(
        event_id=event_id,
        occurred_at=_NOW,
        actor=ActorContext(
            user_id="admin-user",
            role="proxy_admin",
            request_id="request-456",
            action=ActorAction.CHANNEL_CREATE,
            envelope_id=uuid4(),
        ),
        operation_id=operation_id,
        channel_id=_CHANNEL_ID,
        details=ChannelCreateDetails(outcome=SafeAuditOutcome(status=AuditOutcome.ACCEPTED)),
    )


@pytest_asyncio.fixture
async def audit_repository_fixture() -> AsyncIterator[AuditRepositoryFixture]:
    database_url: Final = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")

    schema: Final = f"account_pool_audit_test_{uuid4().hex}"
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as connection:
        await connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            await connection.execute("SELECT set_config('search_path', %s, false)", (schema,))
            await connection.execute(
                b"""
                CREATE TABLE "LiteLLM_AccountPoolEvent" (
                    event_id UUID PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    occurred_at TIMESTAMPTZ NOT NULL,
                    channel_id UUID,
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
                    event_id UUID PRIMARY KEY REFERENCES "LiteLLM_AccountPoolEvent"(event_id),
                    operation_id UUID,
                    actor_role TEXT NOT NULL,
                    actor_action TEXT NOT NULL,
                    actor_envelope_id UUID NOT NULL,
                    outcome TEXT NOT NULL
                );
                """
            )
            yield AuditRepositoryFixture(
                database_url=database_url,
                schema=schema,
                repository=PostgresManagementAuditRepository(database_url, schema=schema),
            )
        finally:
            await connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def test_append_writes_linked_rows_and_round_trips(
    audit_repository_fixture: AuditRepositoryFixture,
) -> None:
    record: Final = _record()

    created: Final = await audit_repository_fixture.repository.append(record)
    loaded: Final = await audit_repository_fixture.repository.load(record.event.event_id)

    assert isinstance(created, AuditWriteSuccess)
    assert created.status == "created"
    assert created.record == record
    assert isinstance(loaded, AuditLoadSuccess)
    assert loaded.record == record


async def test_repeated_identical_event_is_idempotent(
    audit_repository_fixture: AuditRepositoryFixture,
) -> None:
    record: Final = _record()

    assert isinstance(await audit_repository_fixture.repository.append(record), AuditWriteSuccess)
    repeated: Final = await audit_repository_fixture.repository.append(record)

    assert isinstance(repeated, AuditWriteSuccess)
    assert repeated.status == "unchanged"
    assert repeated.record == record


async def test_same_event_id_with_different_content_is_rejected_without_overwrite(
    audit_repository_fixture: AuditRepositoryFixture,
) -> None:
    original: Final = _record()
    conflicting: Final = original.model_copy(
        update={
            "audit": original.audit.model_copy(
                update={"operation_id": UUID("40000000-0000-0000-0000-000000000099")}
            )
        }
    )
    assert isinstance(await audit_repository_fixture.repository.append(original), AuditWriteSuccess)

    result: Final = await audit_repository_fixture.repository.append(conflicting)
    loaded: Final = await audit_repository_fixture.repository.load(_EVENT_ID)

    assert isinstance(result, AuditPersistenceFailure)
    assert result.code == AuditPersistenceFailureCode.CONTENT_CONFLICT
    assert isinstance(loaded, AuditLoadSuccess)
    assert loaded.record == original


async def test_audit_insert_failure_rolls_back_common_event(
    audit_repository_fixture: AuditRepositoryFixture,
) -> None:
    fixture: Final = audit_repository_fixture
    event_id: Final = UUID("40000000-0000-0000-0000-000000000010")
    record: Final = _record(event_id=event_id, operation_id=UUID("40000000-0000-0000-0000-000000000011"))
    async with await psycopg.AsyncConnection.connect(fixture.database_url) as connection, connection.transaction():
        await connection.execute("SELECT set_config('search_path', %s, true)", (fixture.schema,))
        await connection.execute(
            'ALTER TABLE "LiteLLM_AccountPoolAuditEvent" ADD CONSTRAINT reject_test_operation CHECK (operation_id <> %s)',
            (str(record.audit.operation_id),),
        )

    result: Final = await fixture.repository.append(record)
    async with await psycopg.AsyncConnection.connect(fixture.database_url) as connection, connection.transaction():
        await connection.execute("SELECT set_config('search_path', %s, true)", (fixture.schema,))
        cursor: Final = await connection.execute(
            'SELECT 1 FROM "LiteLLM_AccountPoolEvent" WHERE event_id = %s',
            (str(event_id),),
        )
        persisted: Final = await cursor.fetchone()

    assert isinstance(result, AuditPersistenceFailure)
    assert persisted is None


def test_decode_rejects_unregistered_safe_details() -> None:
    record: Final = _record()
    event_row: Final = {
        **record.event.model_dump(mode="python"),
        "safe_details": {
            "kind": "channel_create",
            "outcome": {"status": "accepted"},
            "raw_provider_response": {"credential": "must-not-be-stored"},
        },
    }
    audit_row: Final = record.audit.model_dump(mode="python")

    with pytest.raises(ValidationError):
        decode_management_audit_rows(event_row, audit_row)


@pytest.mark.parametrize("schema", ["", "public; DROP SCHEMA public", "has-dash", "1starts_with_digit"])
def test_repository_rejects_unsafe_schema(schema: str) -> None:
    with pytest.raises(ValueError, match="schema"):
        PostgresManagementAuditRepository("postgresql://localhost/test", schema=schema)
