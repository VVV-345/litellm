"""验证 PostgreSQL 系统运行事件的幂等写入、严格解码和敏感字段边界。"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from account_pool.operational.models import (
    OperationalEventRecord,
    OperationalEventType,
    ParserSnapshotExportTrigger,
    build_parser_snapshot_export_record,
    build_parser_task_operational_record,
)
from account_pool.operational.postgres import PostgresOperationalEventRepository, decode_operational_record
from account_pool.operational.repository import (
    OperationalPersistenceFailure,
    OperationalPersistenceFailureCode,
    OperationalWriteSuccess,
)
from account_pool.parsing.tasks.models import ParserTaskRecord, ParserTaskStatus
from psycopg import sql
from pydantic import ValidationError

_NOW: Final = datetime(2026, 8, 22, 9, 0, tzinfo=UTC)
_CHANNEL_ID: Final = UUID("93000000-0000-0000-0000-000000000001")
_TASK_ID: Final = UUID("93000000-0000-0000-0000-000000000002")
_RUN_ID: Final = UUID("93000000-0000-0000-0000-000000000003")
_OWNER_ID: Final = UUID("93000000-0000-0000-0000-000000000004")
_MIGRATION: Final = (
    Path(__file__).resolve().parents[3]
    / "litellm-proxy-extras"
    / "litellm_proxy_extras"
    / "migrations"
    / "20260822010000_add_account_pool_operational_events"
    / "migration.sql"
)
_EXPANSION_MIGRATION: Final = (
    Path(__file__).resolve().parents[3]
    / "litellm-proxy-extras"
    / "litellm_proxy_extras"
    / "migrations"
    / "20260822020000_expand_account_pool_operational_events"
    / "migration.sql"
)


@dataclass(frozen=True, slots=True)
class OperationalRepositoryFixture:
    repository: PostgresOperationalEventRepository


def _record(provider_id: str = "openai_compatible") -> OperationalEventRecord:
    task: Final = ParserTaskRecord(
        task_id=_TASK_ID,
        channel_id=_CHANNEL_ID,
        parser_run_id=_RUN_ID,
        provider_id=provider_id,
        openai_compatible=True,
        status=ParserTaskStatus.COMPLETED,
        owner_instance_id=_OWNER_ID,
        actor_id="admin-user",
        actor_role="proxy_admin",
        request_id="request-123",
        created_at=_NOW,
        heartbeat_at=_NOW,
        completed_at=_NOW,
    )
    return build_parser_task_operational_record(
        task_id=task.task_id,
        channel_id=task.channel_id,
        parser_run_id=task.parser_run_id,
        provider_id=task.provider_id,
        request_id=task.request_id,
        occurred_at=_NOW,
        event_type=OperationalEventType.PARSER_TASK_COMPLETED,
    )


def _snapshot_record() -> OperationalEventRecord:
    return build_parser_snapshot_export_record(
        channel_id=_CHANNEL_ID,
        parser_run_id=_RUN_ID,
        occurred_at=_NOW,
        event_type=OperationalEventType.PARSER_SNAPSHOT_EXPORTED,
        attempt_count=1,
        trigger=ParserSnapshotExportTrigger.INITIAL,
    )


@pytest_asyncio.fixture
async def operational_repository_fixture() -> AsyncIterator[OperationalRepositoryFixture]:
    database_url: Final = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    schema: Final = f"account_pool_operational_test_{uuid4().hex}"
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
                """
            )
            await connection.execute(_MIGRATION.read_bytes())
            await connection.execute(_EXPANSION_MIGRATION.read_bytes())
            yield OperationalRepositoryFixture(
                repository=PostgresOperationalEventRepository(database_url, schema=schema)
            )
        finally:
            await connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def test_repository_writes_linked_event_idempotently(
    operational_repository_fixture: OperationalRepositoryFixture,
) -> None:
    repository: Final = operational_repository_fixture.repository
    record: Final = _record()

    created: Final = await repository.append(record)
    repeated: Final = await repository.append(record)

    assert isinstance(created, OperationalWriteSuccess)
    assert created.status == "created"
    assert isinstance(repeated, OperationalWriteSuccess)
    assert repeated.status == "unchanged"
    assert isinstance(await repository.append(_snapshot_record()), OperationalWriteSuccess)


async def test_same_event_id_with_changed_content_is_rejected(
    operational_repository_fixture: OperationalRepositoryFixture,
) -> None:
    repository: Final = operational_repository_fixture.repository
    assert isinstance(await repository.append(_record()), OperationalWriteSuccess)

    result: Final = await repository.append(_record(provider_id="another_provider"))

    assert isinstance(result, OperationalPersistenceFailure)
    assert result.code == OperationalPersistenceFailureCode.CONTENT_CONFLICT


def test_decoder_rejects_unregistered_sensitive_columns() -> None:
    record: Final = _record()
    row: Final = {
        "common_event_id": record.event.event_id,
        **record.event.model_dump(exclude={"event_id"}),
        "safe_details": record.event.safe_details.model_dump(),
        "operational_event_id": record.operational.event_id,
        **record.operational.model_dump(exclude={"event_id"}),
        "api_key": "must-not-be-stored",
    }

    with pytest.raises(ValidationError):
        decode_operational_record(row)


def test_migration_contains_only_normalized_operational_fields() -> None:
    migration: Final = _MIGRATION.read_text(encoding="utf-8")
    expansion: Final = _EXPANSION_MIGRATION.read_text(encoding="utf-8")

    assert 'CREATE TABLE "LiteLLM_AccountPoolOperationalEvent"' in migration
    assert '"operation_id" TEXT NOT NULL' in migration
    assert "parser_task" in migration
    assert "parser_snapshot_export" in expansion
    assert "api_key" not in migration.casefold()
    assert "authorization" not in migration.casefold()
    assert "response_body" not in migration.casefold()


@pytest.mark.parametrize("schema", ["", "public; DROP SCHEMA public", "has-dash", "1starts_with_digit"])
def test_repository_rejects_unsafe_schema(schema: str) -> None:
    with pytest.raises(ValueError, match="schema"):
        PostgresOperationalEventRepository("postgresql://localhost/test", schema=schema)
