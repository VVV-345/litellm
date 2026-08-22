"""验证公开元数据 PostgreSQL 队列的幂等调度、竞争认领和状态转换。"""

from __future__ import annotations

import asyncio
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
from account_pool.parsing.public_metadata.models import (
    PublicMetadataTaskFailureCode,
    PublicMetadataTaskRecord,
    PublicMetadataTaskStatus,
)
from account_pool.parsing.public_metadata.postgres import PostgresPublicMetadataTaskRepository
from account_pool.parsing.public_metadata.repository import (
    PublicMetadataClaimSuccess,
    PublicMetadataRecoverySuccess,
    PublicMetadataScheduleSuccess,
    PublicMetadataWriteSuccess,
)
from psycopg import sql
from tests.catalog.test_importer import legacy_config

_NOW: Final = datetime(2026, 8, 22, 11, 0, tzinfo=UTC)
_TASK_ID: Final = UUID("61000000-0000-0000-0000-000000000001")
_RUN_ID: Final = UUID("62000000-0000-0000-0000-000000000002")
_RETRY_RUN_ID: Final = UUID("63000000-0000-0000-0000-000000000003")
_OWNER_ID: Final = UUID("64000000-0000-0000-0000-000000000004")


@dataclass(frozen=True, slots=True)
class PublicMetadataRepositoryFixture:
    repository: PostgresPublicMetadataTaskRepository
    channel_id: UUID


def _migration_sql(pattern: str) -> bytes:
    repository_root: Final = Path(__file__).resolve().parents[3]
    migrations_root: Final = repository_root / "litellm-proxy-extras" / "litellm_proxy_extras" / "migrations"
    matches: Final = tuple(migrations_root.glob(f"*_{pattern}/migration.sql"))
    assert len(matches) == 1
    return matches[0].read_bytes()


@pytest_asyncio.fixture
async def public_metadata_repository_fixture() -> AsyncIterator[PublicMetadataRepositoryFixture]:
    database_url: Final = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    schema: Final = f"account_pool_public_metadata_test_{uuid4().hex}"
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as connection:
        await connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            await connection.execute("SELECT set_config('search_path', %s, false)", (schema,))
            await connection.execute(_migration_sql("add_account_pool_catalog"))
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
                )
                """
            )
            await connection.execute(_migration_sql("add_account_pool_operational_events"))
            await connection.execute(_migration_sql("expand_account_pool_operational_events"))
            await connection.execute(_migration_sql("expand_account_pool_sync_events"))
            await connection.execute(_migration_sql("expand_account_pool_request_events"))
            await connection.execute(_migration_sql("expand_account_pool_restriction_events"))
            await connection.execute(_migration_sql("add_account_pool_public_metadata_tasks"))
            catalog: Final = catalog_import_from_pool_config(legacy_config(), _NOW)
            imported: Final = await PostgresCatalogRepository(database_url, schema=schema).import_once(catalog)
            assert imported.status == "created"
            yield PublicMetadataRepositoryFixture(
                repository=PostgresPublicMetadataTaskRepository(database_url, schema=schema),
                channel_id=catalog.channels[0].channel_id,
            )
        finally:
            await connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _record(channel_id: UUID) -> PublicMetadataTaskRecord:
    return PublicMetadataTaskRecord(
        task_id=_TASK_ID,
        channel_id=channel_id,
        parser_run_id=_RUN_ID,
        provider_id="public_fixture",
        status=PublicMetadataTaskStatus.QUEUED,
        attempt_count=0,
        max_attempts=3,
        next_attempt_at=_NOW,
        created_at=_NOW,
        updated_at=_NOW,
    )


async def test_schedule_is_idempotent_and_concurrent_claim_has_one_owner(
    public_metadata_repository_fixture: PublicMetadataRepositoryFixture,
) -> None:
    fixture: Final = public_metadata_repository_fixture
    record: Final = _record(fixture.channel_id)

    created: Final = await fixture.repository.schedule(record, refresh_after=_NOW - timedelta(days=1))
    repeated: Final = await fixture.repository.schedule(
        record.model_copy(update={"task_id": uuid4(), "parser_run_id": uuid4()}),
        refresh_after=_NOW - timedelta(days=1),
    )
    claims: Final = await asyncio.gather(
        fixture.repository.claim_next(_OWNER_ID, _NOW),
        fixture.repository.claim_next(uuid4(), _NOW),
    )

    assert isinstance(created, PublicMetadataScheduleSuccess) and created.status == "created"
    assert isinstance(repeated, PublicMetadataScheduleSuccess) and repeated.status == "unchanged"
    assert sorted(result.status for result in claims if isinstance(result, PublicMetadataClaimSuccess)) == [
        "claimed",
        "empty",
    ]


async def test_claimed_task_supports_heartbeat_retry_and_completion(
    public_metadata_repository_fixture: PublicMetadataRepositoryFixture,
) -> None:
    fixture: Final = public_metadata_repository_fixture
    assert isinstance(
        await fixture.repository.schedule(_record(fixture.channel_id), refresh_after=_NOW - timedelta(days=1)),
        PublicMetadataScheduleSuccess,
    )
    claimed: Final = await fixture.repository.claim_next(_OWNER_ID, _NOW)
    assert isinstance(claimed, PublicMetadataClaimSuccess) and claimed.record is not None

    heartbeat: Final = await fixture.repository.heartbeat(_TASK_ID, _OWNER_ID, _NOW + timedelta(seconds=5))
    retried: Final = await fixture.repository.retry(
        task_id=_TASK_ID,
        owner_instance_id=_OWNER_ID,
        parser_run_id=_RETRY_RUN_ID,
        failure_code=PublicMetadataTaskFailureCode.SOURCE_TRANSPORT,
        next_attempt_at=_NOW + timedelta(seconds=30),
        at=_NOW + timedelta(seconds=10),
    )
    reclaimed: Final = await fixture.repository.claim_next(_OWNER_ID, _NOW + timedelta(seconds=30))
    completed: Final = await fixture.repository.finish(
        task_id=_TASK_ID,
        owner_instance_id=_OWNER_ID,
        status=PublicMetadataTaskStatus.COMPLETED,
        failure_code=None,
        at=_NOW + timedelta(seconds=35),
    )

    assert isinstance(heartbeat, PublicMetadataWriteSuccess)
    assert isinstance(retried, PublicMetadataWriteSuccess)
    assert retried.record.parser_run_id == _RETRY_RUN_ID
    assert retried.record.failure_code == PublicMetadataTaskFailureCode.SOURCE_TRANSPORT
    assert isinstance(reclaimed, PublicMetadataClaimSuccess) and reclaimed.record is not None
    assert reclaimed.record.attempt_count == 2
    assert isinstance(completed, PublicMetadataWriteSuccess)
    assert completed.record.status == PublicMetadataTaskStatus.COMPLETED


async def test_stale_running_task_is_recovered_for_retry(
    public_metadata_repository_fixture: PublicMetadataRepositoryFixture,
) -> None:
    fixture: Final = public_metadata_repository_fixture
    assert isinstance(
        await fixture.repository.schedule(_record(fixture.channel_id), refresh_after=_NOW - timedelta(days=1)),
        PublicMetadataScheduleSuccess,
    )
    assert isinstance(await fixture.repository.claim_next(_OWNER_ID, _NOW), PublicMetadataClaimSuccess)

    recovered: Final = await fixture.repository.recover_stale(
        stale_before=_NOW + timedelta(seconds=1),
        at=_NOW + timedelta(seconds=2),
    )

    assert isinstance(recovered, PublicMetadataRecoverySuccess)
    assert recovered.records[0].status == PublicMetadataTaskStatus.RETRY_WAIT
    assert recovered.records[0].failure_code == PublicMetadataTaskFailureCode.WORKER_LOST


def test_migration_omits_credentials_urls_and_upstream_payloads() -> None:
    migration: Final = _migration_sql("add_account_pool_public_metadata_tasks").decode("utf-8")

    assert 'FOR UPDATE SKIP LOCKED' not in migration
    assert "public_metadata_task" in migration
    assert "api_key" not in migration.casefold()
    assert "authorization" not in migration.casefold()
    assert "credential_ref" not in migration.casefold()
    assert "api_base" not in migration.casefold()
    assert "response_body" not in migration.casefold()


@pytest.mark.parametrize("schema", ["", "public; DROP SCHEMA public", "has-dash", "1starts_with_digit"])
def test_repository_rejects_unsafe_schema(schema: str) -> None:
    with pytest.raises(ValueError, match="schema"):
        PostgresPublicMetadataTaskRepository("postgresql://localhost/test", schema=schema)
