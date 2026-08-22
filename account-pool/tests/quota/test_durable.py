"""验证额度持久化装饰层的写前事件、失败关闭、代次隔离和重启恢复。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final
from uuid import UUID

from account_pool.models import (
    AccountConfig,
    DeploymentConfig,
    QuotaWindowConfig,
    ReserveRejected,
    ReserveSuccess,
    RuntimeQuotaKind,
    RuntimeQuotaScope,
    RuntimeQuotaWindowType,
    SettleRequest,
)
from account_pool.quota.backend import QuotaBackendWindowState
from account_pool.quota.durable import DurableQuotaStateStore
from account_pool.quota.persistence_models import (
    QuotaGenerationStatus,
    QuotaRecoveryState,
    QuotaRuntimeGeneration,
    QuotaUsageEvent,
    QuotaWindowRuntimeSnapshot,
)
from account_pool.quota.repository import (
    QuotaGenerationWriteSuccess,
    QuotaPersistenceFailure,
    QuotaPersistenceFailureCode,
    QuotaRecoveryLoadSuccess,
    QuotaSnapshotWriteSuccess,
    QuotaUsageWriteSuccess,
)
from account_pool.store import MemoryStateStore

_CHANNEL_ID: Final = UUID("70000000-0000-0000-0000-000000000001")
_OTHER_GENERATION_ID: Final = UUID("70000000-0000-0000-0000-000000000002")
_NOW: Final = datetime.now(tz=UTC).replace(microsecond=0) - timedelta(minutes=1)


class _Clock:
    def __init__(self) -> None:
        self.current = datetime.now(tz=UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


class _QuotaRepository:
    def __init__(self) -> None:
        self.active: QuotaRuntimeGeneration | None = None
        self.generations: dict[UUID, QuotaRuntimeGeneration] = {}
        self.events: dict[UUID, QuotaUsageEvent] = {}
        self.snapshots: dict[tuple[UUID, str, str], QuotaWindowRuntimeSnapshot] = {}
        self.operations: list[str] = []
        self.fail_usage = False

    async def begin_generation(self, generation: QuotaRuntimeGeneration):
        if self.active is not None and self.active.predecessor_generation_id == generation.predecessor_generation_id:
            return QuotaGenerationWriteSuccess(status="unchanged", generation=self.active)
        existing: Final = next(
            (
                item
                for item in self.generations.values()
                if item.status == QuotaGenerationStatus.INITIALIZING
                and item.predecessor_generation_id == generation.predecessor_generation_id
            ),
            None,
        )
        if existing is not None:
            return QuotaGenerationWriteSuccess(status="unchanged", generation=existing)
        self.generations[generation.generation_id] = generation
        self.operations.append("begin")
        return QuotaGenerationWriteSuccess(status="created", generation=generation)

    async def activate_generation(self, generation_id: UUID, at: datetime):
        current: Final = self.generations[generation_id]
        active: Final = current.model_copy(update={"status": QuotaGenerationStatus.ACTIVE, "activated_at": at})
        if self.active is not None:
            self.generations[self.active.generation_id] = self.active.model_copy(
                update={"status": QuotaGenerationStatus.RETIRED, "closed_at": at}
            )
        self.active = active
        self.generations[generation_id] = active
        self.operations.append("activate")
        return QuotaGenerationWriteSuccess(status="updated", generation=active)

    async def fail_generation(self, generation_id: UUID, failure_code: str, at: datetime):
        current: Final = self.generations[generation_id]
        failed: Final = current.model_copy(
            update={
                "status": QuotaGenerationStatus.FAILED,
                "closed_at": at,
                "failure_code": failure_code,
            }
        )
        self.generations[generation_id] = failed
        if self.active is not None and self.active.generation_id == generation_id:
            self.active = None
        self.operations.append("fail")
        return QuotaGenerationWriteSuccess(status="updated", generation=failed)

    async def append_usage(self, events: tuple[QuotaUsageEvent, ...]):
        self.operations.append("usage")
        if self.fail_usage:
            return QuotaPersistenceFailure(
                code=QuotaPersistenceFailureCode.DATABASE_UNAVAILABLE,
                retryable=True,
            )
        for event in events:
            self.events[event.event_id] = event
        return QuotaUsageWriteSuccess(events=events)

    async def save_snapshots(self, snapshots: tuple[QuotaWindowRuntimeSnapshot, ...]):
        self.operations.append("snapshot")
        for snapshot in snapshots:
            self.snapshots[(snapshot.generation_id, snapshot.account_id, snapshot.window_id)] = snapshot
        return QuotaSnapshotWriteSuccess(snapshots=snapshots)

    async def load_active_recovery_state(self):
        if self.active is None:
            return QuotaPersistenceFailure(
                code=QuotaPersistenceFailureCode.ACTIVE_GENERATION_NOT_FOUND,
                retryable=False,
            )
        generation_id: Final = self.active.generation_id
        lineage: Final = _lineage(self.generations, self.active)
        return QuotaRecoveryLoadSuccess(
            state=QuotaRecoveryState(
                generation=self.active,
                windows=tuple(
                    snapshot
                    for (snapshot_generation, _, _), snapshot in self.snapshots.items()
                    if snapshot_generation == generation_id
                ),
                usage_events=tuple(event for event in self.events.values() if event.generation_id in lineage),
            )
        )


def _lineage(
    generations: dict[UUID, QuotaRuntimeGeneration],
    active: QuotaRuntimeGeneration,
) -> frozenset[UUID]:
    predecessor: Final = active.predecessor_generation_id
    if predecessor is None:
        return frozenset((active.generation_id,))
    inherited: Final = _lineage(generations, generations[predecessor])
    return frozenset((active.generation_id, *inherited))


def _account() -> AccountConfig:
    return AccountConfig(
        id="channel-a",
        channel_id=_CHANNEL_ID,
        display_name="Channel A",
        provider="test",
        base_url_display="https://example.test",
        max_concurrency=2,
        quota_windows=(
            QuotaWindowConfig(
                window_id="tokens-five-hour",
                scope=RuntimeQuotaScope.CHANNEL,
                kind=RuntimeQuotaKind.TOKENS,
                window_type=RuntimeQuotaWindowType.ROLLING,
                duration_seconds=18_000,
                limit=Decimal("100"),
                remaining=Decimal("100"),
                observed_at=_NOW.timestamp(),
                source="provider-api",
                reason_code="five_hour_exhausted",
            ),
        ),
        deployments=(DeploymentConfig(public_model="model-a", litellm_model_id="deployment-a"),),
    )


class _BlockingRestoreStore(MemoryStateStore):
    def __init__(self, maximum_lease_seconds: int) -> None:
        super().__init__(maximum_lease_seconds=maximum_lease_seconds)
        self.restore_started = asyncio.Event()
        self.allow_restore = asyncio.Event()
        self.restore_calls = 0

    async def restore_quota_backend(
        self,
        generation_id: UUID,
        windows: tuple[QuotaBackendWindowState, ...],
    ) -> bool:
        self.restore_calls += 1
        self.restore_started.set()
        await self.allow_restore.wait()
        return await super().restore_quota_backend(generation_id, windows)


async def _configured_store(
    repository: _QuotaRepository,
    clock: _Clock,
    backend: MemoryStateStore | None = None,
    maximum_lease_seconds: int = 3_600,
) -> tuple[DurableQuotaStateStore, MemoryStateStore]:
    resolved_backend: Final = backend or MemoryStateStore()
    store: Final = DurableQuotaStateStore(
        resolved_backend,
        repository,
        clock=clock,
        maximum_lease_seconds=maximum_lease_seconds,
    )
    await store.configure((_account(),))
    return store, resolved_backend


async def test_settlement_persists_usage_before_runtime_snapshot() -> None:
    repository: Final = _QuotaRepository()
    clock: Final = _Clock()
    store, _ = await _configured_store(repository, clock)
    reserved: Final = await store.reserve(
        account=_account(),
        deployment_id="deployment-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="request-a",
        estimated_tokens=40,
        ttl_seconds=120,
    )
    assert isinstance(reserved, ReserveSuccess)
    before_settle: Final = len(repository.operations)

    settled: Final = await store.settle(
        SettleRequest(
            lease_id=reserved.lease.lease_id,
            success=True,
            input_tokens=20,
            output_tokens=10,
        )
    )

    assert settled is True
    assert repository.operations[before_settle:] == ["usage", "snapshot"]
    assert tuple(event.amount for event in repository.events.values()) == (Decimal("30"),)


async def test_usage_persistence_failure_closes_generation_and_future_acquire() -> None:
    repository: Final = _QuotaRepository()
    clock: Final = _Clock()
    store, backend = await _configured_store(repository, clock)
    reserved: Final = await store.reserve(
        account=_account(),
        deployment_id="deployment-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="request-a",
        estimated_tokens=10,
        ttl_seconds=120,
    )
    assert isinstance(reserved, ReserveSuccess)
    repository.fail_usage = True

    settled: Final = await store.settle(SettleRequest(lease_id=reserved.lease.lease_id, success=True))
    next_reserve: Final = await store.reserve(
        account=_account(),
        deployment_id="deployment-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="request-b",
        estimated_tokens=10,
        ttl_seconds=120,
    )

    assert settled is False
    assert isinstance(next_reserve, ReserveRejected)
    assert next_reserve.reason == "quota_persistence_unavailable"
    assert await backend.read_quota_generation() is None


async def test_generation_mismatch_recovers_and_rejects_old_callback() -> None:
    repository: Final = _QuotaRepository()
    clock: Final = _Clock()
    store, backend = await _configured_store(repository, clock)
    reserved: Final = await store.reserve(
        account=_account(),
        deployment_id="deployment-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="request-a",
        estimated_tokens=10,
        ttl_seconds=120,
    )
    assert isinstance(reserved, ReserveSuccess)
    active_generation: Final = reserved.lease.generation_id
    await backend.set_quota_generation(_OTHER_GENERATION_ID)

    settled: Final = await store.settle(SettleRequest(lease_id=reserved.lease.lease_id, success=True))

    assert settled is False
    assert active_generation is not None
    assert repository.generations[active_generation].status == QuotaGenerationStatus.RETIRED
    assert repository.active is not None
    assert repository.active.generation_id != active_generation
    assert await backend.read_quota_generation() == repository.active.generation_id


async def test_restart_rebuilds_usage_and_isolates_until_old_reservation_expires() -> None:
    repository: Final = _QuotaRepository()
    clock: Final = _Clock()
    first, _ = await _configured_store(
        repository,
        clock,
        MemoryStateStore(maximum_lease_seconds=180),
        maximum_lease_seconds=180,
    )
    reserved: Final = await first.reserve(
        account=_account(),
        deployment_id="deployment-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="request-a",
        estimated_tokens=40,
        ttl_seconds=120,
    )
    assert isinstance(reserved, ReserveSuccess)
    old_generation: Final = reserved.lease.generation_id

    restarted, backend = await _configured_store(
        repository,
        clock,
        MemoryStateStore(maximum_lease_seconds=180),
        maximum_lease_seconds=180,
    )
    isolated: Final = await restarted.reserve(
        account=_account(),
        deployment_id="deployment-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="request-b",
        estimated_tokens=10,
        ttl_seconds=120,
    )
    clock.advance(181)
    recovered: Final = await restarted.reserve(
        account=_account(),
        deployment_id="deployment-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="request-c",
        estimated_tokens=10,
        ttl_seconds=120,
    )

    assert isinstance(isolated, ReserveRejected)
    assert isolated.reason == "quota_recovery_isolation"
    assert isinstance(recovered, ReserveSuccess)
    assert recovered.lease.generation_id != old_generation
    state: Final = await backend.quota_backend_state("channel-a")
    assert state is not None
    assert state.windows[0].window.reserved == Decimal("10")


async def test_worker_joining_recovered_generation_keeps_shared_isolation_deadline() -> None:
    repository: Final = _QuotaRepository()
    clock: Final = _Clock()
    first, _ = await _configured_store(
        repository,
        clock,
        MemoryStateStore(maximum_lease_seconds=600),
        maximum_lease_seconds=600,
    )
    reserved: Final = await first.reserve(
        account=_account(),
        deployment_id="deployment-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="request-a",
        estimated_tokens=40,
        ttl_seconds=120,
    )
    assert isinstance(reserved, ReserveSuccess)

    recovered_backend: Final = MemoryStateStore(maximum_lease_seconds=600)
    second, _ = await _configured_store(repository, clock, recovered_backend, maximum_lease_seconds=600)
    joined, _ = await _configured_store(repository, clock, recovered_backend, maximum_lease_seconds=600)
    second_result: Final = await second.reserve(
        account=_account(),
        deployment_id="deployment-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="request-b",
        estimated_tokens=10,
        ttl_seconds=120,
    )
    joined_result: Final = await joined.reserve(
        account=_account(),
        deployment_id="deployment-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="request-c",
        estimated_tokens=10,
        ttl_seconds=120,
    )

    assert repository.active is not None
    assert repository.active.isolation_until == clock.current + timedelta(seconds=600)
    assert isinstance(second_result, ReserveRejected)
    assert second_result.reason == "quota_recovery_isolation"
    assert isinstance(joined_result, ReserveRejected)
    assert joined_result.reason == "quota_recovery_isolation"


async def test_only_generation_creator_restores_shared_runtime() -> None:
    repository: Final = _QuotaRepository()
    clock: Final = _Clock()
    first, _ = await _configured_store(repository, clock)
    reserved: Final = await first.reserve(
        account=_account(),
        deployment_id="deployment-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="request-a",
        estimated_tokens=40,
        ttl_seconds=120,
    )
    assert isinstance(reserved, ReserveSuccess)

    backend: Final = _BlockingRestoreStore(maximum_lease_seconds=600)
    leader: Final = DurableQuotaStateStore(backend, repository, clock=clock, maximum_lease_seconds=600)
    follower: Final = DurableQuotaStateStore(backend, repository, clock=clock, maximum_lease_seconds=600)
    leader_configure: Final = asyncio.create_task(leader.configure((_account(),)))
    await backend.restore_started.wait()

    await follower.configure((_account(),))
    unavailable: Final = await follower.reserve(
        account=_account(),
        deployment_id="deployment-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="request-b",
        estimated_tokens=10,
        ttl_seconds=120,
    )
    assert isinstance(unavailable, ReserveRejected)
    assert unavailable.reason == "quota_persistence_unavailable"
    assert backend.restore_calls == 1

    backend.allow_restore.set()
    await leader_configure
    joined: Final = await follower.reserve(
        account=_account(),
        deployment_id="deployment-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="request-c",
        estimated_tokens=10,
        ttl_seconds=120,
    )

    assert isinstance(joined, ReserveRejected)
    assert joined.reason == "quota_recovery_isolation"
    assert backend.restore_calls == 1


async def test_heartbeat_cannot_extend_lease_beyond_absolute_expiry() -> None:
    repository: Final = _QuotaRepository()
    clock: Final = _Clock()
    store, _ = await _configured_store(
        repository,
        clock,
        MemoryStateStore(maximum_lease_seconds=180),
        maximum_lease_seconds=180,
    )
    reserved: Final = await store.reserve(
        account=_account(),
        deployment_id="deployment-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="request-a",
        estimated_tokens=10,
        ttl_seconds=120,
    )
    assert isinstance(reserved, ReserveSuccess)

    assert await store.heartbeat(reserved.lease.lease_id, ttl_seconds=3_600)
    extended: Final = await store.read_lease(reserved.lease.lease_id)

    assert extended is not None
    assert extended.expires_at == extended.absolute_expires_at


async def test_restart_rebuilds_settled_rolling_usage_without_double_counting() -> None:
    repository: Final = _QuotaRepository()
    clock: Final = _Clock()
    first, _ = await _configured_store(repository, clock)
    reserved: Final = await first.reserve(
        account=_account(),
        deployment_id="deployment-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="request-a",
        estimated_tokens=40,
        ttl_seconds=120,
    )
    assert isinstance(reserved, ReserveSuccess)
    assert await first.settle(
        SettleRequest(
            lease_id=reserved.lease.lease_id,
            success=True,
            input_tokens=20,
            output_tokens=10,
        )
    )

    _, backend = await _configured_store(repository, clock, MemoryStateStore())
    state: Final = await backend.quota_backend_state("channel-a")

    assert state is not None
    assert state.windows[0].window.remaining == Decimal("70")
    assert tuple(delta.amount for delta in state.windows[0].window.usage) == (Decimal("30"),)
