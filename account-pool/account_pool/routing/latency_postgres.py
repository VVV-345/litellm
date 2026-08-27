"""使用 PostgreSQL 持久化 Deployment 绑定的延迟 EWMA 恢复快照。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final, cast
from uuid import UUID

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import AsyncRowFactory, dict_row
from pydantic import AwareDatetime, ValidationError

from account_pool.models import FrozenModel
from account_pool.routing.latency import (
    LatencyLoadResult,
    LatencyLoadSuccess,
    LatencyPersistenceFailure,
    LatencyPersistenceFailureCode,
    LatencyWriteResult,
    LatencyWriteSuccess,
    PersistedDeploymentLatency,
)

_SCHEMA_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class _LatencyRow(FrozenModel):
    binding_id: UUID
    ewma_ms: float
    sample_count: int
    observed_at: AwareDatetime


class PostgresLatencyMetricRepository:
    def __init__(self, database_url: str, schema: str = "public") -> None:
        if _SCHEMA_PATTERN.fullmatch(schema) is None:
            raise ValueError("schema must be a valid PostgreSQL identifier")
        self._database_url: Final = database_url
        self._schema: Final = schema

    async def load(self, binding_ids: tuple[UUID, ...]) -> LatencyLoadResult:
        if not binding_ids:
            return LatencyLoadSuccess(metrics=())
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                cursor: Final = await connection.execute(
                    """
                    SELECT binding_id, ewma_ms, sample_count, observed_at
                    FROM "LiteLLM_AccountPoolLatencyMetric"
                    WHERE binding_id = ANY(%s)
                    ORDER BY binding_id
                    """,
                    ([str(binding_id) for binding_id in binding_ids],),
                )
                rows: Final = tuple(_LatencyRow.model_validate(row) for row in await cursor.fetchall())
                return LatencyLoadSuccess(metrics=tuple(_metric(row) for row in rows))
        except (psycopg.DataError, ValidationError):
            return _failure(LatencyPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _failure(LatencyPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def save(self, metric: PersistedDeploymentLatency) -> LatencyWriteResult:
        try:
            async with await self._connect() as connection, connection.transaction():
                await self._set_search_path(connection)
                cursor: Final = await connection.execute(
                    """
                    INSERT INTO "LiteLLM_AccountPoolLatencyMetric" (
                        binding_id, ewma_ms, sample_count, observed_at, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT (binding_id) DO UPDATE SET
                        ewma_ms = EXCLUDED.ewma_ms,
                        sample_count = EXCLUDED.sample_count,
                        observed_at = EXCLUDED.observed_at,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE "LiteLLM_AccountPoolLatencyMetric".observed_at < EXCLUDED.observed_at
                       OR (
                           "LiteLLM_AccountPoolLatencyMetric".observed_at = EXCLUDED.observed_at
                           AND "LiteLLM_AccountPoolLatencyMetric".sample_count < EXCLUDED.sample_count
                       )
                    RETURNING binding_id, ewma_ms, sample_count, observed_at
                    """,
                    (str(metric.binding_id), metric.ewma_ms, metric.sample_count, metric.observed_at),
                )
                row: Final = await cursor.fetchone()
                if row is None:
                    current_cursor: Final = await connection.execute(
                        """
                        SELECT binding_id, ewma_ms, sample_count, observed_at
                        FROM "LiteLLM_AccountPoolLatencyMetric"
                        WHERE binding_id = %s
                        """,
                        (str(metric.binding_id),),
                    )
                    current: Final = await current_cursor.fetchone()
                    if current is None:
                        return _failure(LatencyPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)
                    return LatencyWriteSuccess(metric=_metric(_LatencyRow.model_validate(current)))
                return LatencyWriteSuccess(metric=_metric(_LatencyRow.model_validate(row)))
        except (psycopg.DataError, ValidationError):
            return _failure(LatencyPersistenceFailureCode.INVALID_STORED_DATA, retryable=False)
        except psycopg.Error:
            return _failure(LatencyPersistenceFailureCode.DATABASE_UNAVAILABLE, retryable=True)

    async def _connect(self) -> AsyncConnection[Mapping[str, object]]:
        row_factory: Final = cast(AsyncRowFactory[Mapping[str, object]], dict_row)
        return await AsyncConnection[Mapping[str, object]].connect(self._database_url, row_factory=row_factory)

    async def _set_search_path(self, connection: AsyncConnection[Mapping[str, object]]) -> None:
        await connection.execute("SELECT set_config('search_path', %s, true)", (self._schema,))


def _metric(row: _LatencyRow) -> PersistedDeploymentLatency:
    return PersistedDeploymentLatency(
        binding_id=row.binding_id,
        ewma_ms=row.ewma_ms,
        sample_count=row.sample_count,
        observed_at=row.observed_at,
    )


def _failure(code: LatencyPersistenceFailureCode, retryable: bool) -> LatencyPersistenceFailure:
    return LatencyPersistenceFailure(code=code, retryable=retryable)
