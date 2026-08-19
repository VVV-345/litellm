"""验证解析任务 PostgreSQL 仓储的迁移、所有权心跳、完成和过期中断。"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from account_pool.catalog.importer import catalog_import_from_pool_config
from account_pool.catalog.postgres import PostgresCatalogRepository
from account_pool.parsing.tasks.models import ParserTaskRecord, ParserTaskStatus
from account_pool.parsing.tasks.postgres import PostgresParserTaskRepository
from account_pool.parsing.tasks.repository import (
    ParserTaskLoadSuccess,
    ParserTaskSweepSuccess,
    ParserTaskWriteSuccess,
)
from psycopg import sql
from tests.catalog.test_importer import legacy_config

_NOW: Final = datetime(2026, 8, 19, 23, 0, tzinfo=UTC)
_TASK_ID: Final = UUID("20000000-0000-0000-0000-000000000002")
_RUN_ID: Final = UUID("30000000-0000-0000-0000-000000000003")
_OWNER_ID: Final = UUID("40000000-0000-0000-0000-000000000004")


@dataclass(frozen=True, slots=True)
class TaskRepositoryFixture:
    repository: PostgresParserTaskRepository
    channel_id: UUID


def _migration_sql(pattern: str) -> bytes:
    repository_root: Final = Path(__file__).resolve().parents[3]
    migrations_root: Final = repository_root / "litellm-proxy-extras" / "litellm_proxy_extras" / "migrations"
    matches: Final = tuple(migrations_root.glob(f"*_{pattern}/migration.sql"))
    assert len(matches) == 1
    return matches[0].read_bytes()


@pytest_asyncio.fixture
async def task_repository_fixture() -> AsyncIterator[TaskRepositoryFixture]:
    database_url: Final = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    schema: Final = f"account_pool_task_test_{uuid4().hex}"
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as connection:
        await connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            await connection.execute("SELECT set_config('search_path', %s, false)", (schema,))
            await connection.execute(_migration_sql("add_account_pool_catalog"))
            await connection.execute(_migration_sql("add_account_pool_parser_tasks"))
            catalog: Final = catalog_import_from_pool_config(legacy_config(), _NOW)
            imported: Final = await PostgresCatalogRepository(database_url, schema=schema).import_once(catalog)
            assert imported.status == "created"
            yield TaskRepositoryFixture(
                repository=PostgresParserTaskRepository(database_url, schema=schema),
                channel_id=catalog.channels[0].channel_id,
            )
        finally:
            await connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _record(channel_id: UUID, heartbeat_at: datetime = _NOW) -> ParserTaskRecord:
    return ParserTaskRecord(
        task_id=_TASK_ID,
        channel_id=channel_id,
        parser_run_id=_RUN_ID,
        provider_id="openai_compatible",
        explicit_parser_id="openai-compatible",
        openai_compatible=True,
        status=ParserTaskStatus.RUNNING,
        owner_instance_id=_OWNER_ID,
        actor_id="admin-user",
        actor_role="proxy_admin",
        request_id="request-123",
        created_at=_NOW,
        heartbeat_at=heartbeat_at,
    )


async def test_repository_round_trips_heartbeat_and_completion(
    task_repository_fixture: TaskRepositoryFixture,
) -> None:
    fixture: Final = task_repository_fixture
    record: Final = _record(fixture.channel_id)

    created: Final = await fixture.repository.create(record)
    heartbeat: Final = await fixture.repository.heartbeat(
        task_id=_TASK_ID,
        owner_instance_id=_OWNER_ID,
        at=_NOW + timedelta(seconds=5),
    )
    completed: Final = await fixture.repository.finish(
        task_id=_TASK_ID,
        owner_instance_id=_OWNER_ID,
        status=ParserTaskStatus.COMPLETED,
        failure_code=None,
        at=_NOW + timedelta(seconds=10),
    )
    loaded: Final = await fixture.repository.load(fixture.channel_id, _TASK_ID)

    assert isinstance(created, ParserTaskWriteSuccess)
    assert isinstance(heartbeat, ParserTaskWriteSuccess)
    assert heartbeat.record.heartbeat_at == _NOW + timedelta(seconds=5)
    assert isinstance(completed, ParserTaskWriteSuccess)
    assert isinstance(loaded, ParserTaskLoadSuccess)
    assert loaded.record.status == ParserTaskStatus.COMPLETED
    assert loaded.record.completed_at == _NOW + timedelta(seconds=10)
    assert "api_key" not in loaded.model_dump_json()
    assert "api_base" not in loaded.model_dump_json()


async def test_sweeper_marks_only_stale_running_tasks_interrupted(
    task_repository_fixture: TaskRepositoryFixture,
) -> None:
    fixture: Final = task_repository_fixture
    assert isinstance(
        await fixture.repository.create(_record(fixture.channel_id, heartbeat_at=_NOW - timedelta(minutes=1))),
        ParserTaskWriteSuccess,
    )

    swept: Final = await fixture.repository.sweep_stale(
        stale_before=_NOW - timedelta(seconds=30),
        at=_NOW,
    )
    loaded: Final = await fixture.repository.load(fixture.channel_id, _TASK_ID)

    assert isinstance(swept, ParserTaskSweepSuccess)
    assert swept.interrupted_task_ids == (_TASK_ID,)
    assert isinstance(loaded, ParserTaskLoadSuccess)
    assert loaded.record.status == ParserTaskStatus.INTERRUPTED_REQUIRES_KEY
