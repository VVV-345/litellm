"""验证成功请求延迟 EWMA 的计算和持久化时间转换。"""

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from account_pool.routing.latency import DeploymentLatencyMetric, persisted_latency, update_latency_ewma

_BINDING_ID: Final = UUID("90000000-0000-0000-0000-000000000001")


def test_latency_ewma_uses_first_sample_then_fixed_alpha() -> None:
    first: Final = update_latency_ewma(None, "deployment-a", latency_ms=100, observed_at=1_000)
    second: Final = update_latency_ewma(first, "deployment-a", latency_ms=200, observed_at=2_000)

    assert first == DeploymentLatencyMetric(
        deployment_id="deployment-a",
        ewma_ms=100,
        sample_count=1,
        observed_at=1_000,
    )
    assert second == DeploymentLatencyMetric(
        deployment_id="deployment-a",
        ewma_ms=120,
        sample_count=2,
        observed_at=2_000,
    )


def test_persisted_latency_uses_stable_binding_identity_and_utc_time() -> None:
    metric: Final = DeploymentLatencyMetric(
        deployment_id="runtime-deployment-a",
        ewma_ms=125.5,
        sample_count=3,
        observed_at=1_700_000_000,
    )

    persisted: Final = persisted_latency(_BINDING_ID, metric)

    assert persisted.binding_id == _BINDING_ID
    assert persisted.ewma_ms == 125.5
    assert persisted.sample_count == 3
    assert persisted.observed_at == datetime.fromtimestamp(1_700_000_000, tz=UTC)
