"""验证延迟持久化装饰层的绑定恢复、幂等写入和容错行为。"""

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from account_pool.models import AccountConfig, DeploymentConfig, ReserveSuccess, SettleRequest
from account_pool.routing.latency import (
    LatencyLoadSuccess,
    LatencyPersistenceFailure,
    LatencyPersistenceFailureCode,
    LatencyWriteSuccess,
    PersistedDeploymentLatency,
)
from account_pool.routing.latency_store import DurableLatencyStateStore
from account_pool.store import MemoryStateStore

_BINDING_ID: Final = UUID("91000000-0000-0000-0000-000000000001")
_OTHER_BINDING_ID: Final = UUID("91000000-0000-0000-0000-000000000002")
_OBSERVED_AT: Final = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)


class _LatencyRepository:
    def __init__(self, metrics: tuple[PersistedDeploymentLatency, ...] = ()) -> None:
        self.metrics = {metric.binding_id: metric for metric in metrics}
        self.loaded: tuple[UUID, ...] = ()
        self.saved: list[PersistedDeploymentLatency] = []
        self.fail_load = False
        self.fail_save = False

    async def load(self, binding_ids: tuple[UUID, ...]):
        self.loaded = binding_ids
        if self.fail_load:
            return LatencyPersistenceFailure(
                code=LatencyPersistenceFailureCode.DATABASE_UNAVAILABLE,
                retryable=True,
            )
        return LatencyLoadSuccess(metrics=tuple(self.metrics[item] for item in binding_ids if item in self.metrics))

    async def save(self, metric: PersistedDeploymentLatency):
        self.saved.append(metric)
        if self.fail_save:
            return LatencyPersistenceFailure(
                code=LatencyPersistenceFailureCode.DATABASE_UNAVAILABLE,
                retryable=True,
            )
        self.metrics[metric.binding_id] = metric
        return LatencyWriteSuccess(metric=metric)


def _account(deployment_id: str = "deployment-a", binding_id: UUID | None = _BINDING_ID) -> AccountConfig:
    return AccountConfig(
        id="channel-a",
        display_name="Channel A",
        provider="test",
        base_url_display="https://example.test",
        max_concurrency=2,
        deployments=(
            DeploymentConfig(
                public_model="model-a",
                litellm_model_id=deployment_id,
                binding_id=binding_id,
            ),
        ),
    )


async def _reserve(store: DurableLatencyStateStore, account: AccountConfig, request_id: str) -> ReserveSuccess:
    reserved: Final = await store.reserve(
        account=account,
        deployment_id=account.deployments[0].litellm_model_id,
        billing_route_id=None,
        public_model="model-a",
        request_id=request_id,
        estimated_tokens=0,
        ttl_seconds=60,
    )
    assert isinstance(reserved, ReserveSuccess)
    return reserved


async def test_restore_maps_stable_binding_to_current_deployment_id() -> None:
    persisted: Final = PersistedDeploymentLatency(
        binding_id=_BINDING_ID,
        ewma_ms=80,
        sample_count=4,
        observed_at=_OBSERVED_AT,
    )
    repository: Final = _LatencyRepository((persisted,))
    store: Final = DurableLatencyStateStore(MemoryStateStore(), repository)

    await store.configure((_account(deployment_id="replacement-deployment"),))

    metrics: Final = await store.latency_metrics()
    assert repository.loaded == (_BINDING_ID,)
    assert len(metrics) == 1
    assert metrics[0].deployment_id == "replacement-deployment"
    assert metrics[0].ewma_ms == 80
    assert metrics[0].sample_count == 4


async def test_settlement_persists_once_and_ignores_failed_zero_and_probe_samples() -> None:
    repository: Final = _LatencyRepository()
    account: Final = _account()
    store: Final = DurableLatencyStateStore(MemoryStateStore(), repository)
    await store.configure((account,))

    successful: Final = await _reserve(store, account, "successful")
    assert await store.settle(
        SettleRequest(lease_id=successful.lease.lease_id, success=True, latency_ms=100)
    )
    assert await store.settle(
        SettleRequest(lease_id=successful.lease.lease_id, success=True, latency_ms=900)
    )
    assert await store.release(successful.lease.lease_id)
    failed: Final = await _reserve(store, account, "failed")
    assert await store.settle(SettleRequest(lease_id=failed.lease.lease_id, success=False, latency_ms=50))
    assert await store.release(failed.lease.lease_id)
    zero: Final = await _reserve(store, account, "zero")
    assert await store.settle(SettleRequest(lease_id=zero.lease.lease_id, success=True, latency_ms=0))
    assert await store.release(zero.lease.lease_id)
    probe: Final = await store.reserve(
        account=account,
        deployment_id="deployment-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="probe",
        estimated_tokens=0,
        ttl_seconds=60,
        probe=True,
    )
    assert isinstance(probe, ReserveSuccess)
    assert await store.settle(SettleRequest(lease_id=probe.lease.lease_id, success=True, latency_ms=25))

    metrics: Final = await store.latency_metrics()
    assert len(repository.saved) == 1
    assert len(metrics) == 1
    assert metrics[0].ewma_ms == 100
    assert metrics[0].sample_count == 1


async def test_persistence_failure_does_not_fail_request_settlement() -> None:
    repository: Final = _LatencyRepository()
    repository.fail_save = True
    account: Final = _account()
    store: Final = DurableLatencyStateStore(MemoryStateStore(), repository)
    await store.configure((account,))
    reserved: Final = await _reserve(store, account, "request-a")

    settled: Final = await store.settle(
        SettleRequest(lease_id=reserved.lease.lease_id, success=True, latency_ms=75)
    )

    assert settled is True
    assert len(repository.saved) == 1
    assert (await store.latency_metrics())[0].ewma_ms == 75


async def test_load_failure_preserves_newer_realtime_metric_on_reconfigure() -> None:
    repository: Final = _LatencyRepository()
    account: Final = _account()
    store: Final = DurableLatencyStateStore(MemoryStateStore(), repository)
    await store.configure((account,))
    reserved: Final = await _reserve(store, account, "request-a")
    assert await store.settle(SettleRequest(lease_id=reserved.lease.lease_id, success=True, latency_ms=90))
    repository.fail_load = True

    await store.configure((account,))

    metrics: Final = await store.latency_metrics()
    assert len(metrics) == 1
    assert metrics[0].ewma_ms == 90
    assert metrics[0].sample_count == 1


async def test_unbound_legacy_deployment_keeps_runtime_metric_without_persisting() -> None:
    repository: Final = _LatencyRepository()
    account: Final = _account(binding_id=None)
    store: Final = DurableLatencyStateStore(MemoryStateStore(), repository)
    await store.configure((account,))
    reserved: Final = await _reserve(store, account, "request-a")

    assert await store.settle(SettleRequest(lease_id=reserved.lease.lease_id, success=True, latency_ms=110))

    assert repository.loaded == ()
    assert repository.saved == []
    assert (await store.latency_metrics())[0].ewma_ms == 110


async def test_restore_ignores_metrics_for_unconfigured_bindings() -> None:
    repository: Final = _LatencyRepository(
        (
            PersistedDeploymentLatency(
                binding_id=_OTHER_BINDING_ID,
                ewma_ms=45,
                sample_count=2,
                observed_at=_OBSERVED_AT,
            ),
        )
    )
    store: Final = DurableLatencyStateStore(MemoryStateStore(), repository)

    await store.configure((_account(),))

    assert await store.latency_metrics() == ()
