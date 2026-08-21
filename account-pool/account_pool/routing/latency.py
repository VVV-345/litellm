"""定义成功请求延迟 EWMA 的运行模型、更新规则和持久化协议。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, Field

from account_pool.models import FrozenModel

LATENCY_EWMA_ALPHA: Final = 0.2


class DeploymentLatencyMetric(FrozenModel):
    deployment_id: str = Field(min_length=1)
    ewma_ms: float = Field(gt=0)
    sample_count: int = Field(ge=1)
    observed_at: float = Field(ge=0)


class PersistedDeploymentLatency(FrozenModel):
    binding_id: UUID
    ewma_ms: float = Field(gt=0)
    sample_count: int = Field(ge=1)
    observed_at: AwareDatetime


class LatencyPersistenceFailureCode(StrEnum):
    INVALID_STORED_DATA = "invalid_stored_data"
    DATABASE_UNAVAILABLE = "database_unavailable"


class LatencyPersistenceFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: LatencyPersistenceFailureCode
    retryable: bool


class LatencyLoadSuccess(FrozenModel):
    status: Literal["loaded"] = "loaded"
    metrics: tuple[PersistedDeploymentLatency, ...]


class LatencyWriteSuccess(FrozenModel):
    status: Literal["persisted"] = "persisted"
    metric: PersistedDeploymentLatency


LatencyLoadResult = LatencyLoadSuccess | LatencyPersistenceFailure
LatencyWriteResult = LatencyWriteSuccess | LatencyPersistenceFailure


class LatencyMetricRepository(Protocol):
    async def load(self, binding_ids: tuple[UUID, ...]) -> LatencyLoadResult: ...

    async def save(self, metric: PersistedDeploymentLatency) -> LatencyWriteResult: ...


def update_latency_ewma(
    current: DeploymentLatencyMetric | None,
    deployment_id: str,
    latency_ms: float,
    observed_at: float,
) -> DeploymentLatencyMetric:
    ewma: Final = (
        latency_ms
        if current is None
        else LATENCY_EWMA_ALPHA * latency_ms + (1 - LATENCY_EWMA_ALPHA) * current.ewma_ms
    )
    return DeploymentLatencyMetric(
        deployment_id=deployment_id,
        ewma_ms=ewma,
        sample_count=1 if current is None else current.sample_count + 1,
        observed_at=observed_at,
    )


def persisted_latency(
    binding_id: UUID,
    metric: DeploymentLatencyMetric,
) -> PersistedDeploymentLatency:
    return PersistedDeploymentLatency(
        binding_id=binding_id,
        ewma_ms=metric.ewma_ms,
        sample_count=metric.sample_count,
        observed_at=datetime.fromtimestamp(metric.observed_at, tz=UTC),
    )
