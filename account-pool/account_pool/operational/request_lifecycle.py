"""记录请求申请、结算、释放和租约过期的脱敏运行事件。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Final

from account_pool.eligibility import EligibilityExclusion
from account_pool.models import (
    AccountConfig,
    AccountSnapshot,
    AcquireCandidateRejection,
    Lease,
    ReserveResult,
    SettleRequest,
)
from account_pool.quota.backend import QuotaBackendState
from account_pool.operational.models import (
    OperationalEventRecord,
    build_lease_expired_record,
    build_request_acquire_failed_record,
    build_request_acquired_record,
    build_request_released_record,
    build_request_settled_record,
    build_request_usage_recorded_record,
)
from account_pool.operational.repository import (
    OperationalEventRepository,
    OperationalWriteSuccess,
)
from account_pool.routing.latency import DeploymentLatencyMetric
from account_pool.store import StateStore

_LOGGER: Final = logging.getLogger(__name__)


class RequestEventRecorder:
    def __init__(
        self,
        repository: OperationalEventRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository: Final = repository
        self._clock: Final = clock or _utc_now

    async def acquired(
        self,
        account: AccountConfig,
        lease: Lease,
        estimated_tokens: int,
        lease_ttl_seconds: int,
    ) -> None:
        if account.channel_id is None:
            return
        await self._append(
            build_request_acquired_record(
                channel_id=account.channel_id,
                lease=lease,
                estimated_tokens=estimated_tokens,
                occurred_at=datetime.fromtimestamp(lease.expires_at, UTC) - timedelta(seconds=lease_ttl_seconds),
            )
        )

    async def acquire_failed(
        self,
        account: AccountConfig,
        model: str,
        request_id: str,
        rejection: AcquireCandidateRejection,
    ) -> None:
        if account.channel_id is None:
            return
        await self._append(
            build_request_acquire_failed_record(
                channel_id=account.channel_id,
                model_id=model,
                request_id=request_id,
                rejection=rejection,
                occurred_at=self._clock(),
            )
        )

    async def settled(self, account: AccountConfig, lease: Lease, request: SettleRequest, applied: bool) -> None:
        if account.channel_id is None:
            return
        await self._append(
            build_request_settled_record(
                channel_id=account.channel_id,
                lease=lease,
                request=request,
                applied=applied,
                occurred_at=self._clock(),
            )
        )
        if applied and (request.input_tokens > 0 or request.output_tokens > 0 or request.cost_usd is not None):
            await self._append(
                build_request_usage_recorded_record(
                    channel_id=account.channel_id,
                    lease=lease,
                    request=request,
                    occurred_at=self._clock(),
                )
            )

    async def released(self, account: AccountConfig, lease: Lease, released: bool) -> None:
        if account.channel_id is None:
            return
        await self._append(
            build_request_released_record(
                channel_id=account.channel_id,
                lease=lease,
                released=released,
                occurred_at=self._clock(),
            )
        )

    async def lease_expired(self, account: AccountConfig, lease: Lease) -> None:
        if account.channel_id is None:
            return
        await self._append(
            build_lease_expired_record(
                channel_id=account.channel_id,
                lease=lease,
                occurred_at=self._clock(),
            )
        )

    async def _append(self, record: OperationalEventRecord) -> None:
        result: Final = await self._repository.append(record)
        if not isinstance(result, OperationalWriteSuccess):
            _LOGGER.error("Failed to persist request lifecycle event: %s", result.code)


class RequestEventStateStore:
    def __init__(self, backend: StateStore, recorder: RequestEventRecorder) -> None:
        self._backend: Final = backend
        self._recorder: Final = recorder
        self._accounts: dict[str, AccountConfig] = {}

    async def configure(self, accounts: tuple[AccountConfig, ...]) -> None:
        self._accounts = {account.id: account for account in accounts}
        await self._backend.configure(accounts)

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
        account: Final = None if lease is None else self._accounts.get(lease.account_id)
        if lease is not None and account is not None and not lease.probe and not lease.settled:
            await self._recorder.settled(account, lease, request, settled)
        return settled

    async def read_lease(self, lease_id: str) -> Lease | None:
        return await self._backend.read_lease(lease_id)

    async def release(self, lease_id: str) -> bool:
        lease: Final = await self._backend.read_lease(lease_id)
        released: Final = await self._backend.release(lease_id)
        account: Final = None if lease is None else self._accounts.get(lease.account_id)
        if lease is not None and account is not None and not lease.probe and not lease.released:
            await self._recorder.released(account, lease, released)
        return released

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
        expired: Final = await self._backend.sweep_expired()
        await asyncio.gather(*(self._record_expired(lease) for lease in expired))
        return expired

    async def close(self) -> None:
        await self._backend.close()

    async def _record_expired(self, lease: Lease) -> None:
        account: Final = self._accounts.get(lease.account_id)
        if account is not None and not lease.probe:
            await self._recorder.lease_expired(account, lease)


def _utc_now() -> datetime:
    return datetime.now(UTC)
