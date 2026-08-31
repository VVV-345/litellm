"""用 PostgreSQL 快照增强实时状态存储，使 Deployment 延迟指标可跨重启恢复。"""

from __future__ import annotations

import logging
from typing import Final
from uuid import UUID

from account_pool.eligibility import EligibilityExclusion
from account_pool.models import AccountConfig, AccountSnapshot, Lease, ReserveResult, SettleRequest
from account_pool.quota.backend import QuotaBackendState
from account_pool.routing.latency import (
    DeploymentLatencyMetric,
    LatencyLoadSuccess,
    LatencyMetricRepository,
    LatencyWriteSuccess,
    persisted_latency,
)
from account_pool.store import StateStore

_LOGGER: Final = logging.getLogger(__name__)


class DurableLatencyStateStore:
    def __init__(self, backend: StateStore, repository: LatencyMetricRepository) -> None:
        self._backend: Final = backend
        self._repository: Final = repository
        self._bindings: dict[str, UUID] = {}

    async def configure(self, accounts: tuple[AccountConfig, ...]) -> None:
        self._bindings = {
            deployment.litellm_model_id: deployment.binding_id
            for account in accounts
            for deployment in account.deployments
            if deployment.binding_id is not None
        }
        await self._backend.configure(accounts)
        loaded: Final = await self._repository.load(tuple(self._bindings.values()))
        if not isinstance(loaded, LatencyLoadSuccess):
            _LOGGER.error("Failed to restore latency metrics: %s", loaded.code)
            return
        deployments_by_binding: Final = {
            binding_id: deployment_id for deployment_id, binding_id in self._bindings.items()
        }
        await self._backend.restore_latency_metrics(
            tuple(
                DeploymentLatencyMetric(
                    deployment_id=deployments_by_binding[metric.binding_id],
                    ewma_ms=metric.ewma_ms,
                    sample_count=metric.sample_count,
                    observed_at=metric.observed_at.timestamp(),
                )
                for metric in loaded.metrics
                if metric.binding_id in deployments_by_binding
            )
        )

    async def snapshots(self) -> tuple[AccountSnapshot, ...]:
        return await self._backend.snapshots()

    async def eligibility_exclusions(self) -> tuple[EligibilityExclusion, ...]:
        return await self._backend.eligibility_exclusions()

    async def quota_backend_state(self, account_id: str | None = None) -> QuotaBackendState | None:
        return await self._backend.quota_backend_state(account_id)

    async def reserve(
        self,
        account: AccountConfig,
        deployment_id: str,
        billing_route_id: str | None,
        public_model: str,
        request_id: str,
        estimated_tokens: int,
        ttl_seconds: int,
        probe: bool = False,
    ) -> ReserveResult:
        return await self._backend.reserve(
            account=account,
            deployment_id=deployment_id,
            billing_route_id=billing_route_id,
            public_model=public_model,
            request_id=request_id,
            estimated_tokens=estimated_tokens,
            ttl_seconds=ttl_seconds,
            probe=probe,
        )

    async def settle(self, request: SettleRequest) -> bool:
        lease: Final = await self._backend.read_lease(request.lease_id)
        settled: Final = await self._backend.settle(request)
        if not settled or lease is None or lease.settled or not _records_latency(lease, request):
            return settled
        metric: Final = next(
            (
                current
                for current in await self._backend.latency_metrics()
                if current.deployment_id == lease.deployment_id
            ),
            None,
        )
        binding_id: Final = self._bindings.get(lease.deployment_id)
        if metric is None or binding_id is None:
            return settled
        saved: Final = await self._repository.save(persisted_latency(binding_id, metric))
        if not isinstance(saved, LatencyWriteSuccess):
            _LOGGER.error("Failed to persist latency metric: %s", saved.code)
        return settled

    async def read_lease(self, lease_id: str) -> Lease | None:
        return await self._backend.read_lease(lease_id)

    async def release(self, lease_id: str) -> bool:
        return await self._backend.release(lease_id)

    async def heartbeat(self, lease_id: str, ttl_seconds: int) -> bool:
        return await self._backend.heartbeat(lease_id, ttl_seconds)

    async def next_sequence(self, model: str) -> int:
        return await self._backend.next_sequence(model)

    async def latency_metrics(self) -> tuple[DeploymentLatencyMetric, ...]:
        return await self._backend.latency_metrics()

    async def restore_latency_metrics(self, metrics: tuple[DeploymentLatencyMetric, ...]) -> None:
        await self._backend.restore_latency_metrics(metrics)

    async def set_latency_metric(self, metric: DeploymentLatencyMetric) -> None:
        await self._backend.set_latency_metric(metric)

    async def sweep_expired(self) -> tuple[Lease, ...]:
        return await self._backend.sweep_expired()

    async def close(self) -> None:
        await self._backend.close()


def _records_latency(lease: Lease, request: SettleRequest) -> bool:
    return request.success and request.latency_ms is not None and request.latency_ms > 0 and not lease.probe
