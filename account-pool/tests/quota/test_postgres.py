"""验证 PostgreSQL 额度仓储的代次切换、幂等 usage、快照和恢复读取。"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from account_pool.models import (
    AccountConfig,
    DeploymentConfig,
    Lease,
    QuotaWindowConfig,
    RuntimeQuotaKind,
    RuntimeQuotaScope,
    RuntimeQuotaWindowType,
    SettleRequest,
)
from account_pool.quota.persistence_models import (
    QuotaGenerationStatus,
    QuotaRuntimeGeneration,
    QuotaUsageEvent,
    build_quota_usage_events,
    build_quota_window_snapshot,
)
from account_pool.quota.postgres import PostgresQuotaRuntimeRepository, decode_usage_row
from account_pool.quota.repository import (
    QuotaGenerationWriteSuccess,
    QuotaPersistenceFailure,
    QuotaPersistenceFailureCode,
    QuotaRecoveryLoadSuccess,
    QuotaSnapshotWriteSuccess,
    QuotaUsageWriteSuccess,
)
from account_pool.quota.runtime import RuntimeQuotaWindow
from psycopg import sql
from pydantic import ValidationError

_GENERATION_ID: Final = UUID("60000000-0000-0000-0000-000000000001")
_CHANNEL_ID: Final = UUID("60000000-0000-0000-0000-000000000002")
_NOW: Final = datetime(2026, 8, 19, 13, 0, tzinfo=UTC)
_MIGRATION: Final = (
    Path(__file__).resolve().parents[3]
    / "litellm-proxy-extras"
    / "litellm_proxy_extras"
    / "migrations"
    / "20260819230000_add_account_pool_quota_runtime"
    / "migration.sql"
)


@dataclass(frozen=True, slots=True)
class QuotaRepositoryFixture:
    database_url: str
    schema: str
    repository: PostgresQuotaRuntimeRepository


def _generation() -> QuotaRuntimeGeneration:
    return QuotaRuntimeGeneration(
        generation_id=_GENERATION_ID,
        status=QuotaGenerationStatus.INITIALIZING,
        created_at=_NOW,
    )


def _runtime_window() -> RuntimeQuotaWindow:
    config: Final = QuotaWindowConfig(
        window_id="window-a",
        scope=RuntimeQuotaScope.CHANNEL,
        kind=RuntimeQuotaKind.TOKENS,
        window_type=RuntimeQuotaWindowType.ROLLING,
        duration_seconds=18_000,
        limit=Decimal("1000.123456789123456789123456789123456789"),
        remaining=Decimal("750.123456789123456789123456789123456789"),
        observed_at=_NOW.timestamp(),
        source="provider-api",
        reason_code="five_hour_exhausted",
    )
    return RuntimeQuotaWindow(
        config=config,
        remaining=config.remaining,
        retry_at=(_NOW + timedelta(hours=5)).timestamp(),
    )


def _account(window: RuntimeQuotaWindow) -> AccountConfig:
    return AccountConfig(
        id="channel-a",
        channel_id=_CHANNEL_ID,
        display_name="Channel A",
        provider="test",
        base_url_display="https://example.test",
        max_concurrency=1,
        quota_windows=(window.config,),
        deployments=(DeploymentConfig(public_model="model-a", litellm_model_id="deployment-a"),),
    )


def _usage_event() -> QuotaUsageEvent:
    window: Final = _runtime_window()
    account: Final = _account(window)
    lease: Final = Lease(
        lease_id="lease-a",
        request_id="request-a",
        account_id=account.id,
        deployment_id="deployment-a",
        public_model="model-a",
        expires_at=(_NOW + timedelta(minutes=2)).timestamp(),
    )
    events: Final = build_quota_usage_events(
        generation_id=_GENERATION_ID,
        account=account,
        lease=lease,
        request=SettleRequest(lease_id=lease.lease_id, success=True, input_tokens=25, output_tokens=5),
        windows=(window,),
        occurred_at=_NOW,
    )
    return events[0]


@pytest_asyncio.fixture
async def quota_repository_fixture() -> AsyncIterator[QuotaRepositoryFixture]:
    database_url: Final = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")

    schema: Final = f"account_pool_quota_test_{uuid4().hex}"
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as connection:
        await connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            await connection.execute("SELECT set_config('search_path', %s, false)", (schema,))
            await connection.execute(_MIGRATION.read_bytes())
            yield QuotaRepositoryFixture(
                database_url=database_url,
                schema=schema,
                repository=PostgresQuotaRuntimeRepository(database_url, schema=schema),
            )
        finally:
            await connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def test_repository_round_trips_active_generation_usage_and_snapshot(
    quota_repository_fixture: QuotaRepositoryFixture,
) -> None:
    repository: Final = quota_repository_fixture.repository
    generation: Final = _generation()
    event: Final = _usage_event()
    window: Final = _runtime_window()
    snapshot: Final = build_quota_window_snapshot(
        generation_id=_GENERATION_ID,
        account=_account(window),
        window=window,
        captured_at=_NOW,
    )

    created: Final = await repository.begin_generation(generation)
    activated: Final = await repository.activate_generation(_GENERATION_ID, _NOW + timedelta(seconds=1))
    usage_written: Final = await repository.append_usage((event,))
    snapshot_written: Final = await repository.save_snapshots((snapshot,))
    loaded: Final = await repository.load_active_recovery_state()

    assert isinstance(created, QuotaGenerationWriteSuccess)
    assert created.status == "created"
    assert isinstance(activated, QuotaGenerationWriteSuccess)
    assert activated.generation.status == QuotaGenerationStatus.ACTIVE
    assert isinstance(usage_written, QuotaUsageWriteSuccess)
    assert isinstance(snapshot_written, QuotaSnapshotWriteSuccess)
    assert isinstance(loaded, QuotaRecoveryLoadSuccess)
    assert loaded.state.generation == activated.generation
    assert loaded.state.usage_events == (event,)
    assert loaded.state.windows == (snapshot,)


async def test_usage_append_is_idempotent_and_rejects_changed_content(
    quota_repository_fixture: QuotaRepositoryFixture,
) -> None:
    repository: Final = quota_repository_fixture.repository
    event: Final = _usage_event()
    assert isinstance(await repository.begin_generation(_generation()), QuotaGenerationWriteSuccess)

    first: Final = await repository.append_usage((event,))
    repeated: Final = await repository.append_usage((event,))
    conflicting: Final = await repository.append_usage((event.model_copy(update={"amount": Decimal("31")}),))

    assert isinstance(first, QuotaUsageWriteSuccess)
    assert isinstance(repeated, QuotaUsageWriteSuccess)
    assert isinstance(conflicting, QuotaPersistenceFailure)
    assert conflicting.code == QuotaPersistenceFailureCode.CONTENT_CONFLICT


def test_usage_decoder_rejects_unregistered_sensitive_columns() -> None:
    event: Final = _usage_event()
    row: Final = {**event.model_dump(), "api_key": "must-not-be-stored"}

    with pytest.raises(ValidationError):
        decode_usage_row(row)


@pytest.mark.parametrize("schema", ["", "public; DROP SCHEMA public", "has-dash", "1starts_with_digit"])
def test_repository_rejects_unsafe_schema(schema: str) -> None:
    with pytest.raises(ValueError, match="schema"):
        PostgresQuotaRuntimeRepository("postgresql://localhost/test", schema=schema)
