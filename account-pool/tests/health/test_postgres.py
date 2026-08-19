"""验证 PostgreSQL 健康仓储的事件幂等、活动时间单调更新和安全解码。"""

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
from account_pool.eligibility import EligibilityScope
from account_pool.health.models import HealthEventRecord, HealthRequestActivity, build_passive_health_record
from account_pool.health.postgres import PostgresHealthEventRepository, decode_health_record
from account_pool.health.repository import (
    HealthActivityLoadSuccess,
    HealthActivityWriteSuccess,
    HealthEventListSuccess,
    HealthLoadSuccess,
    HealthPersistenceFailure,
    HealthPersistenceFailureCode,
    HealthWriteSuccess,
)
from account_pool.models import AccountConfig, DeploymentConfig, Lease, SettleRequest
from psycopg import sql
from pydantic import ValidationError

_NOW: Final = datetime(2026, 8, 20, 3, 0, tzinfo=UTC)
_CHANNEL_ID: Final = UUID("82000000-0000-0000-0000-000000000001")
_MIGRATION: Final = (
    Path(__file__).resolve().parents[3]
    / "litellm-proxy-extras"
    / "litellm_proxy_extras"
    / "migrations"
    / "20260820010000_add_account_pool_health_events"
    / "migration.sql"
)


@dataclass(frozen=True, slots=True)
class HealthRepositoryFixture:
    database_url: str
    schema: str
    repository: PostgresHealthEventRepository


def _account() -> AccountConfig:
    return AccountConfig(
        id="channel-a",
        channel_id=_CHANNEL_ID,
        display_name="Channel A",
        provider="test",
        base_url_display="https://provider.example/v1",
        max_concurrency=1,
        deployments=(DeploymentConfig(public_model="model-a", litellm_model_id="deployment-a"),),
    )


def _record() -> HealthEventRecord:
    return build_passive_health_record(
        account=_account(),
        lease=Lease(
            lease_id="lease-a",
            request_id="request-a",
            account_id="channel-a",
            deployment_id="deployment-a",
            public_model="model-a",
            expires_at=(_NOW + timedelta(minutes=1)).timestamp(),
        ),
        request=SettleRequest(lease_id="lease-a", success=True, status_code=200, latency_ms=25),
        occurred_at=_NOW,
        scope=EligibilityScope.DEPLOYMENT,
    )


@pytest_asyncio.fixture
async def health_repository_fixture() -> AsyncIterator[HealthRepositoryFixture]:
    database_url: Final = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")

    schema: Final = f"account_pool_health_test_{uuid4().hex}"
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
            yield HealthRepositoryFixture(
                database_url=database_url,
                schema=schema,
                repository=PostgresHealthEventRepository(database_url, schema=schema),
            )
        finally:
            await connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def test_repository_round_trips_event_and_updates_activity(
    health_repository_fixture: HealthRepositoryFixture,
) -> None:
    repository: Final = health_repository_fixture.repository
    record: Final = _record()

    created: Final = await repository.append(record)
    repeated: Final = await repository.append(record)
    loaded: Final = await repository.load(record.event.event_id)
    activities: Final = await repository.load_activity()
    recent: Final = await repository.list_recent(_CHANNEL_ID)

    assert isinstance(created, HealthWriteSuccess)
    assert created.status == "created"
    assert isinstance(repeated, HealthWriteSuccess)
    assert repeated.status == "unchanged"
    assert isinstance(loaded, HealthLoadSuccess)
    assert loaded.record == record
    assert isinstance(activities, HealthActivityLoadSuccess)
    assert activities.activities[0].last_request_at == _NOW
    assert activities.activities[0].last_success_at == _NOW
    assert isinstance(recent, HealthEventListSuccess)
    assert recent.records == (record,)


async def test_request_activity_updates_monotonically(
    health_repository_fixture: HealthRepositoryFixture,
) -> None:
    repository: Final = health_repository_fixture.repository
    latest: Final = HealthRequestActivity(
        channel_id=_CHANNEL_ID,
        account_id="channel-a",
        model_id="model-a",
        deployment_id="deployment-a",
        observed_at=_NOW,
    )
    older: Final = latest.model_copy(update={"observed_at": _NOW - timedelta(hours=1)})

    assert isinstance(await repository.record_request(latest), HealthActivityWriteSuccess)
    assert isinstance(await repository.record_request(older), HealthActivityWriteSuccess)
    loaded: Final = await repository.load_activity()

    assert isinstance(loaded, HealthActivityLoadSuccess)
    assert loaded.activities[0].last_request_at == _NOW


async def test_same_event_id_with_changed_content_is_rejected(
    health_repository_fixture: HealthRepositoryFixture,
) -> None:
    repository: Final = health_repository_fixture.repository
    record: Final = _record()
    assert isinstance(await repository.append(record), HealthWriteSuccess)

    result: Final = await repository.append(
        record.model_copy(
            update={"health": record.health.model_copy(update={"scope": EligibilityScope.CHANNEL})}
        )
    )

    assert isinstance(result, HealthPersistenceFailure)
    assert result.code == HealthPersistenceFailureCode.CONTENT_CONFLICT


def test_health_decoder_rejects_unregistered_sensitive_columns() -> None:
    record: Final = _record()
    row: Final = {
        "common_event_id": record.event.event_id,
        **record.event.model_dump(exclude={"event_id"}),
        "safe_details": record.event.safe_details.model_dump(),
        "health_event_id": record.health.event_id,
        **record.health.model_dump(exclude={"event_id"}),
        "api_key": "must-not-be-stored",
    }

    with pytest.raises(ValidationError):
        decode_health_record(row)


def test_health_migration_contains_only_normalized_runtime_fields() -> None:
    migration: Final = _MIGRATION.read_text(encoding="utf-8")

    assert 'CREATE TABLE "LiteLLM_AccountPoolHealthEvent"' in migration
    assert 'CREATE TABLE "LiteLLM_AccountPoolHealthActivity"' in migration
    assert '"last_request_at" TIMESTAMPTZ(6)' in migration
    assert '"last_probe_at" TIMESTAMPTZ(6)' in migration
    assert "api_key" not in migration.casefold()
    assert "authorization" not in migration.casefold()
    assert "response_body" not in migration.casefold()


@pytest.mark.parametrize("schema", ["", "public; DROP SCHEMA public", "has-dash", "1starts_with_digit"])
def test_repository_rejects_unsafe_schema(schema: str) -> None:
    with pytest.raises(ValueError, match="schema"):
        PostgresHealthEventRepository("postgresql://localhost/test", schema=schema)
