"""验证 PostgreSQL 延迟快照仓储的恢复、陈旧写入和数据校验。"""

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
from account_pool.routing.latency import (
    LatencyLoadSuccess,
    LatencyPersistenceFailure,
    LatencyPersistenceFailureCode,
    LatencyWriteSuccess,
    PersistedDeploymentLatency,
)
from account_pool.routing.latency_postgres import PostgresLatencyMetricRepository
from psycopg import sql

_CHANNEL_ID: Final = UUID("92000000-0000-0000-0000-000000000001")
_BINDING_ID: Final = UUID("92000000-0000-0000-0000-000000000002")
_NOW: Final = datetime(2026, 8, 21, 9, 0, tzinfo=UTC)
_MIGRATIONS_ROOT: Final = (
    Path(__file__).resolve().parents[3]
    / "litellm-proxy-extras"
    / "litellm_proxy_extras"
    / "migrations"
)


def _migration(pattern: str) -> bytes:
    matches: Final = tuple(_MIGRATIONS_ROOT.glob(f"*_{pattern}/migration.sql"))
    assert len(matches) == 1
    return matches[0].read_bytes()


@dataclass(frozen=True, slots=True)
class LatencyRepositoryFixture:
    database_url: str
    schema: str
    repository: PostgresLatencyMetricRepository


@pytest_asyncio.fixture
async def latency_repository_fixture() -> AsyncIterator[LatencyRepositoryFixture]:
    database_url: Final = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    schema: Final = f"account_pool_latency_test_{uuid4().hex}"
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as connection:
        await connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            await connection.execute("SELECT set_config('search_path', %s, false)", (schema,))
            await connection.execute(_migration("add_account_pool_catalog"))
            await connection.execute(
                """
                INSERT INTO "LiteLLM_AccountPoolChannel" (
                    channel_id, account_order, display_name, provider, base_url_display,
                    administrative_state, max_concurrency, priority, weight, quota_unit, updated_at
                ) VALUES (%s, 0, 'Channel A', 'test', 'https://example.test', 'enabled', 1, 0, 1, 'tokens', %s)
                """,
                (str(_CHANNEL_ID), _NOW),
            )
            await connection.execute(
                """
                INSERT INTO "LiteLLM_AccountPoolBinding" (
                    binding_id, channel_id, deployment_order, public_model,
                    litellm_deployment_id, ownership, enabled, updated_at
                ) VALUES (%s, %s, 0, 'model-a', 'deployment-a', 'pool_managed', true, %s)
                """,
                (str(_BINDING_ID), str(_CHANNEL_ID), _NOW),
            )
            await connection.execute(_migration("add_account_pool_latency_metrics"))
            yield LatencyRepositoryFixture(
                database_url=database_url,
                schema=schema,
                repository=PostgresLatencyMetricRepository(database_url, schema=schema),
            )
        finally:
            await connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def test_repository_round_trips_metric_and_keeps_newest_snapshot(
    latency_repository_fixture: LatencyRepositoryFixture,
) -> None:
    repository: Final = latency_repository_fixture.repository
    first: Final = PersistedDeploymentLatency(
        binding_id=_BINDING_ID,
        ewma_ms=100,
        sample_count=1,
        observed_at=_NOW,
    )
    newer: Final = first.model_copy(
        update={"ewma_ms": 120, "sample_count": 2, "observed_at": _NOW + timedelta(seconds=1)}
    )
    stale: Final = first.model_copy(
        update={"ewma_ms": 40, "sample_count": 9, "observed_at": _NOW - timedelta(seconds=1)}
    )

    assert isinstance(await repository.save(first), LatencyWriteSuccess)
    assert isinstance(await repository.save(newer), LatencyWriteSuccess)
    stale_result: Final = await repository.save(stale)
    loaded: Final = await repository.load((_BINDING_ID,))

    assert isinstance(stale_result, LatencyWriteSuccess)
    assert stale_result.metric == newer
    assert isinstance(loaded, LatencyLoadSuccess)
    assert loaded.metrics == (newer,)


async def test_equal_timestamp_keeps_higher_sample_count(
    latency_repository_fixture: LatencyRepositoryFixture,
) -> None:
    repository: Final = latency_repository_fixture.repository
    lower: Final = PersistedDeploymentLatency(
        binding_id=_BINDING_ID,
        ewma_ms=100,
        sample_count=1,
        observed_at=_NOW,
    )
    higher: Final = lower.model_copy(update={"ewma_ms": 110, "sample_count": 2})

    assert isinstance(await repository.save(higher), LatencyWriteSuccess)
    result: Final = await repository.save(lower)

    assert isinstance(result, LatencyWriteSuccess)
    assert result.metric == higher


async def test_invalid_stored_metric_returns_typed_failure(
    latency_repository_fixture: LatencyRepositoryFixture,
) -> None:
    fixture: Final = latency_repository_fixture
    async with await psycopg.AsyncConnection.connect(fixture.database_url, autocommit=True) as connection:
        await connection.execute("SELECT set_config('search_path', %s, false)", (fixture.schema,))
        await connection.execute(
            'ALTER TABLE "LiteLLM_AccountPoolLatencyMetric" DROP CONSTRAINT "LiteLLM_AccountPoolLatencyMetric_sample_count_check"'
        )
        await connection.execute(
            """
            INSERT INTO "LiteLLM_AccountPoolLatencyMetric" (
                binding_id, ewma_ms, sample_count, observed_at, created_at, updated_at
            ) VALUES (%s, 100, 0, %s, %s, %s)
            """,
            (str(_BINDING_ID), _NOW, _NOW, _NOW),
        )

    loaded: Final = await fixture.repository.load((_BINDING_ID,))

    assert isinstance(loaded, LatencyPersistenceFailure)
    assert loaded.code == LatencyPersistenceFailureCode.INVALID_STORED_DATA
    assert loaded.retryable is False


@pytest.mark.parametrize("schema", ["", "public; DROP SCHEMA public", "has-dash", "1starts_with_digit"])
def test_repository_rejects_unsafe_schema(schema: str) -> None:
    with pytest.raises(ValueError, match="schema"):
        PostgresLatencyMetricRepository("postgresql://localhost/test", schema=schema)


def test_prisma_latency_blocks_are_identical() -> None:
    repository_root: Final = Path(__file__).resolve().parents[3]
    schemas: Final = (
        repository_root / "schema.prisma",
        repository_root / "litellm" / "proxy" / "schema.prisma",
        repository_root / "litellm-proxy-extras" / "litellm_proxy_extras" / "schema.prisma",
    )
    blocks: Final = tuple(_prisma_latency_block(path.read_text(encoding="utf-8")) for path in schemas)

    assert blocks[0] == blocks[1] == blocks[2]


def _prisma_latency_block(schema: str) -> str:
    start: Final = schema.index("model LiteLLM_AccountPoolLatencyMetric {")
    end: Final = schema.index("\n}\n", start) + 2
    return schema[start:end]
