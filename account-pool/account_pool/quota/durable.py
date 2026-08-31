"""以装饰器接入额度写前事件、运行快照、代次门禁和重启恢复。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol
from uuid import UUID, uuid4

from account_pool.eligibility import EligibilityExclusion
from account_pool.models import (
    AccountConfig,
    AccountSnapshot,
    Lease,
    ReserveRejected,
    ReserveResult,
    ReserveSuccess,
    SettleRequest,
)
from account_pool.quota.backend import QuotaBackendState, QuotaBackendWindowState, QuotaRuntimeBackend
from account_pool.quota.persistence_models import (
    QuotaGenerationStatus,
    QuotaRecoveryState,
    QuotaRuntimeGeneration,
    QuotaUsageEvent,
    QuotaWindowRuntimeSnapshot,
    build_quota_usage_events,
    build_quota_window_snapshot,
    restore_quota_window,
)
from account_pool.quota.repository import (
    QuotaGenerationWriteSuccess,
    QuotaPersistenceFailure,
    QuotaPersistenceFailureCode,
    QuotaRecoveryLoadSuccess,
    QuotaRuntimeRepository,
    QuotaSnapshotWriteSuccess,
    QuotaUsageWriteSuccess,
)
from account_pool.quota.runtime import RuntimeQuotaWindow, reconcile_quota_windows
from account_pool.routing.latency import DeploymentLatencyMetric
from account_pool.store import StateStore

_LOGGER: Final = logging.getLogger(__name__)


class DurableQuotaBackend(StateStore, QuotaRuntimeBackend, Protocol):
    pass


class DurableQuotaStateStore:
    def __init__(
        self,
        backend: DurableQuotaBackend,
        repository: QuotaRuntimeRepository,
        clock: Callable[[], datetime] | None = None,
        maximum_lease_seconds: int = 3_600,
    ) -> None:
        if maximum_lease_seconds < 1:
            raise ValueError("maximum_lease_seconds must be positive")
        self._backend: Final = backend
        self._repository: Final = repository
        self._clock: Final = clock or _utc_now
        self._maximum_lease_seconds: Final = maximum_lease_seconds
        self._recovery_lock: Final = asyncio.Lock()
        self._accounts: dict[str, AccountConfig] = {}
        self._generation_id: UUID | None = None
        self._isolation_until: datetime | None = None
        self._initialized = False
        self._ready = False
        self._join_pending = False

    async def configure(self, accounts: tuple[AccountConfig, ...]) -> None:
        self._accounts = {account.id: account for account in accounts}
        await self._backend.configure(accounts)
        if not self._initialized:
            await self._initialize()
            return
        if self._ready and not await self._persist_snapshots():
            await self._fail_closed("quota_snapshot_persistence_failed")

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
        rejection: Final = await self._generation_rejection()
        if rejection is not None:
            return ReserveRejected(reason=rejection)
        result: Final = await self._backend.reserve(
            account=account,
            deployment_id=deployment_id,
            billing_route_id=billing_route_id,
            public_model=public_model,
            request_id=request_id,
            estimated_tokens=estimated_tokens,
            ttl_seconds=ttl_seconds,
            probe=probe,
        )
        if not isinstance(result, ReserveSuccess):
            return result
        if result.lease.generation_id != self._generation_id:
            await self._fail_closed("quota_lease_generation_mismatch")
            return ReserveRejected(reason="quota_generation_mismatch")
        if await self._persist_snapshots(account.id):
            return result
        await self._backend.release(result.lease.lease_id)
        await self._fail_closed("quota_snapshot_persistence_failed")
        return ReserveRejected(reason="quota_persistence_unavailable")

    async def settle(self, request: SettleRequest) -> bool:
        if await self._generation_rejection() is not None:
            return False
        lease: Final = await self._backend.read_lease(request.lease_id)
        generation_id: Final = self._generation_id
        if lease is None or generation_id is None or lease.generation_id != generation_id:
            return False
        account: Final = self._accounts.get(lease.account_id)
        before: Final = await self._backend.quota_backend_state(lease.account_id)
        if account is None or before is None or before.generation_id != generation_id:
            await self._fail_closed("quota_runtime_state_invalid")
            return False
        events: Final = build_quota_usage_events(
            generation_id=generation_id,
            account=account,
            lease=lease,
            request=request,
            windows=tuple(state.window for state in before.windows),
            occurred_at=self._clock(),
        )
        usage_result: Final = await self._repository.append_usage(events)
        if not isinstance(usage_result, QuotaUsageWriteSuccess):
            await self._fail_closed("quota_usage_persistence_failed")
            return False
        if not await self._backend.settle(request):
            # usage 已写入而运行态未确认时必须关闭代次，恢复会保守地应用这笔 usage。
            await self._fail_closed("quota_runtime_settle_failed")
            return False
        if await self._persist_snapshots(lease.account_id):
            return True
        await self._fail_closed("quota_snapshot_persistence_failed")
        return False

    async def read_lease(self, lease_id: str) -> Lease | None:
        return await self._valid_lease(lease_id)

    async def release(self, lease_id: str) -> bool:
        lease: Final = await self._valid_lease(lease_id)
        if lease is None:
            return False
        released: Final = await self._backend.release(lease_id)
        if not released:
            return False
        if await self._persist_snapshots(lease.account_id):
            return True
        await self._fail_closed("quota_snapshot_persistence_failed")
        return False

    async def heartbeat(self, lease_id: str, ttl_seconds: int) -> bool:
        lease: Final = await self._valid_lease(lease_id)
        if lease is None:
            return False
        extended: Final = await self._backend.heartbeat(lease_id, ttl_seconds)
        if not extended:
            return False
        if await self._persist_snapshots(lease.account_id):
            return True
        await self._fail_closed("quota_snapshot_persistence_failed")
        return False

    async def next_sequence(self, model: str) -> int:
        return await self._backend.next_sequence(model)

    async def latency_metrics(self) -> tuple[DeploymentLatencyMetric, ...]:
        return await self._backend.latency_metrics()

    async def restore_latency_metrics(self, metrics: tuple[DeploymentLatencyMetric, ...]) -> None:
        await self._backend.restore_latency_metrics(metrics)

    async def set_latency_metric(self, metric: DeploymentLatencyMetric) -> None:
        await self._backend.set_latency_metric(metric)

    async def sweep_expired(self) -> tuple[Lease, ...]:
        released: Final = await self._backend.sweep_expired()
        if released and self._ready and not await self._persist_snapshots():
            await self._fail_closed("quota_snapshot_persistence_failed")
        return released

    async def close(self) -> None:
        await self._backend.close()

    async def _initialize(self) -> None:
        recovery_result: Final = await self._repository.load_active_recovery_state()
        backend_generation: Final = await self._backend.read_quota_generation()
        if isinstance(recovery_result, QuotaRecoveryLoadSuccess):
            active: Final = recovery_result.state.generation
            if backend_generation == active.generation_id:
                self._generation_id = active.generation_id
                self._isolation_until = active.isolation_until
                self._initialized = True
                self._ready = True
                self._join_pending = False
                if not await self._persist_snapshots():
                    await self._fail_closed("quota_snapshot_persistence_failed")
                return
            await self._recover_new_generation(recovery_result.state)
            return
        if recovery_result.code != QuotaPersistenceFailureCode.ACTIVE_GENERATION_NOT_FOUND:
            self._initialized = True
            await self._fail_closed("quota_recovery_load_failed")
            return
        await self._recover_new_generation(None)

    async def _recover_new_generation(self, recovery: QuotaRecoveryState | None) -> None:
        now: Final = self._clock()
        backend_state: Final = await self._backend.quota_backend_state()
        if backend_state is None:
            self._initialized = True
            await self._fail_closed("quota_runtime_state_invalid")
            return
        generation: Final = QuotaRuntimeGeneration(
            generation_id=uuid4(),
            predecessor_generation_id=None if recovery is None else recovery.generation.generation_id,
            status=QuotaGenerationStatus.INITIALIZING,
            created_at=now,
            isolation_until=None if recovery is None else now + timedelta(seconds=self._maximum_lease_seconds),
        )
        begun: Final = await self._repository.begin_generation(generation)
        if not isinstance(begun, QuotaGenerationWriteSuccess):
            self._initialized = True
            await self._fail_closed("quota_generation_begin_failed")
            return
        selected_generation: Final = begun.generation
        if selected_generation.status == QuotaGenerationStatus.ACTIVE:
            self._generation_id = selected_generation.generation_id
            self._isolation_until = selected_generation.isolation_until
            self._initialized = True
            self._ready = await self._backend.read_quota_generation() == selected_generation.generation_id
            self._join_pending = False
            if not self._ready:
                await self._fail_closed("quota_generation_join_failed")
            return
        if begun.status == "unchanged":
            self._generation_id = selected_generation.generation_id
            self._isolation_until = selected_generation.isolation_until
            self._initialized = True
            self._ready = False
            self._join_pending = True
            return
        restored_windows: Final = _recovered_backend_windows(
            current=backend_state.windows,
            recovery=recovery,
            now=now,
        )
        if not await self._backend.restore_quota_backend(selected_generation.generation_id, restored_windows):
            self._generation_id = selected_generation.generation_id
            self._initialized = True
            await self._fail_closed("quota_runtime_restore_failed")
            return
        self._generation_id = selected_generation.generation_id
        self._isolation_until = selected_generation.isolation_until
        if not await self._persist_snapshots():
            self._initialized = True
            await self._fail_closed("quota_snapshot_persistence_failed")
            return
        activated: Final = await self._repository.activate_generation(selected_generation.generation_id, now)
        self._initialized = True
        if not isinstance(activated, QuotaGenerationWriteSuccess):
            await self._fail_closed("quota_generation_activation_failed")
            return
        self._isolation_until = activated.generation.isolation_until
        self._ready = True
        self._join_pending = False

    async def _persist_snapshots(self, account_id: str | None = None) -> bool:
        generation_id: Final = self._generation_id
        if generation_id is None:
            return False
        state: Final = await self._backend.quota_backend_state(account_id)
        if state is None or state.generation_id != generation_id:
            return False
        captured_at: Final = self._clock()
        snapshots: Final = tuple(
            build_quota_window_snapshot(
                generation_id=generation_id,
                account=self._accounts[window.account_id],
                window=window.window,
                captured_at=captured_at,
                reservation_expires_at=(
                    None
                    if window.reservation_expires_at is None
                    else datetime.fromtimestamp(window.reservation_expires_at, tz=UTC)
                ),
            )
            for window in state.windows
        )
        result: Final = await self._repository.save_snapshots(snapshots)
        return isinstance(result, QuotaSnapshotWriteSuccess)

    async def _generation_rejection(self) -> str | None:
        if not self._ready or self._generation_id is None:
            if not self._join_pending:
                return "quota_persistence_unavailable"
            initialized_generation: Final = await self._synchronize_runtime_generation()
            if initialized_generation is None:
                return "quota_persistence_unavailable"
        if await self._backend.read_quota_generation() != self._generation_id:
            synchronized_generation: Final = await self._synchronize_runtime_generation()
            if synchronized_generation is None:
                return "quota_generation_mismatch"
            if await self._backend.read_quota_generation() != synchronized_generation:
                return "quota_generation_mismatch"
        if self._isolation_until is not None and self._isolation_until > self._clock():
            return "quota_recovery_isolation"
        return None

    async def _synchronize_runtime_generation(self) -> UUID | None:
        async with self._recovery_lock:
            backend_generation: Final = await self._backend.read_quota_generation()
            if self._ready and self._generation_id is not None and backend_generation == self._generation_id:
                return self._generation_id
            self._ready = False
            self._initialized = False
            await self._initialize()
            return self._generation_id if self._ready else None

    async def _valid_lease(self, lease_id: str) -> Lease | None:
        if await self._generation_rejection() is not None:
            return None
        lease: Final = await self._backend.read_lease(lease_id)
        return lease if lease is not None and lease.generation_id == self._generation_id else None

    async def _fail_closed(self, failure_code: str) -> None:
        generation_id: Final = self._generation_id
        self._ready = False
        self._join_pending = False
        backend_generation: Final = await self._backend.read_quota_generation()
        if generation_id is not None and backend_generation == generation_id:
            await self._backend.set_quota_generation(None)
        if generation_id is not None:
            result: Final = await self._repository.fail_generation(generation_id, failure_code, self._clock())
            if isinstance(result, QuotaPersistenceFailure):
                _LOGGER.error("Failed to persist quota generation failure: %s", result.code)
        _LOGGER.error("Quota runtime entered fail-closed state: %s", failure_code)


def _recovered_backend_windows(
    *,
    current: tuple[QuotaBackendWindowState, ...],
    recovery: QuotaRecoveryState | None,
    now: datetime,
) -> tuple[QuotaBackendWindowState, ...]:
    snapshots: Final = (
        {}
        if recovery is None
        else {(snapshot.account_id, snapshot.window_id): snapshot for snapshot in recovery.windows}
    )
    usage_events: Final = () if recovery is None else recovery.usage_events
    return tuple(
        QuotaBackendWindowState(
            account_id=state.account_id,
            window=_recovered_window(
                current=state.window,
                snapshot=snapshots.get((state.account_id, state.window.config.window_id)),
                usage_events=usage_events,
                now=now,
            ),
        )
        for state in current
    )


def _recovered_window(
    *,
    current: RuntimeQuotaWindow,
    snapshot: QuotaWindowRuntimeSnapshot | None,
    usage_events: tuple[QuotaUsageEvent, ...],
    now: datetime,
) -> RuntimeQuotaWindow:
    if snapshot is None:
        return replace(current, reserved=current.reserved * 0)
    restored: Final = restore_quota_window(snapshot, usage_events, now)
    reconciled: Final = reconcile_quota_windows((restored,), (current.config,))[0]
    return replace(reconciled, reserved=reconciled.reserved * 0)


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)
