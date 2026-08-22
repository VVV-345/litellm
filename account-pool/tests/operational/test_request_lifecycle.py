"""验证请求生命周期事件的脱敏、关联和状态存储接入行为。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from account_pool.models import (
    AccountConfig,
    AcquireCandidateRejection,
    AcquireRejectionScope,
    AcquireRejectionSource,
    AcquireRejectionStage,
    AcquireRequest,
    AcquireSuccess,
    AcquireUnavailable,
    DeploymentConfig,
    PoolConfig,
    ReserveSuccess,
    SettleRequest,
)
from account_pool.operational.models import (
    OperationalEventRecord,
    OperationalEventType,
    build_request_acquire_failed_record,
)
from account_pool.operational.repository import OperationalWriteResult, OperationalWriteSuccess
from account_pool.operational.request_lifecycle import RequestEventRecorder, RequestEventStateStore
from account_pool.scheduler import Scheduler
from account_pool.store import MemoryStateStore

_NOW: Final = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
_CHANNEL_ID: Final = UUID("94000000-0000-0000-0000-000000000001")


class RecordingRepository:
    def __init__(self) -> None:
        self.records: tuple[OperationalEventRecord, ...] = ()

    async def append(self, record: OperationalEventRecord) -> OperationalWriteResult:
        self.records = (*self.records, record)
        return OperationalWriteSuccess(status="created", record=record)


def _account() -> AccountConfig:
    return AccountConfig(
        id="channel-account",
        channel_id=_CHANNEL_ID,
        display_name="Channel",
        provider="openai_compatible",
        base_url_display="https://example.test",
        max_concurrency=2,
        deployments=(DeploymentConfig(public_model="model-a", litellm_model_id="deployment-a"),),
    )


async def _reserve(store: RequestEventStateStore, request_id: str = "request-1") -> ReserveSuccess:
    account: Final = _account()
    await store.configure((account,))
    result: Final = await store.reserve(
        account=account,
        deployment_id="deployment-a",
        billing_route_id=None,
        public_model="model-a",
        request_id=request_id,
        estimated_tokens=10,
        ttl_seconds=60,
    )
    assert isinstance(result, ReserveSuccess)
    return result


async def test_state_store_records_settle_and_release_once() -> None:
    repository: Final = RecordingRepository()
    recorder: Final = RequestEventRecorder(repository, clock=lambda: _NOW)
    store: Final = RequestEventStateStore(MemoryStateStore(), recorder)
    reserved: Final = await _reserve(store)
    settlement: Final = SettleRequest(
        lease_id=reserved.lease.lease_id,
        success=True,
        status_code=200,
        input_tokens=12,
        output_tokens=4,
        latency_ms=25,
    )

    assert await store.settle(settlement)
    assert await store.settle(settlement)
    assert await store.release(reserved.lease.lease_id)
    assert await store.release(reserved.lease.lease_id)

    assert tuple(record.event.event_type for record in repository.records) == (
        OperationalEventType.REQUEST_SETTLED,
        OperationalEventType.REQUEST_USAGE_RECORDED,
        OperationalEventType.REQUEST_RELEASED,
    )
    settled: Final = repository.records[0].event
    assert settled.channel_id == _CHANNEL_ID
    assert settled.request_id == "request-1"
    assert settled.safe_details.model_dump(mode="json") == {
        "kind": "request_settled",
        "applied": True,
        "success": True,
        "status_code": 200,
        "input_tokens": 12,
        "output_tokens": 4,
        "cost_usd": None,
        "latency_ms": 25.0,
    }
    usage: Final = repository.records[1].event
    assert usage.safe_details.model_dump(mode="json") == {
        "kind": "request_usage_recorded",
        "input_tokens": 12,
        "output_tokens": 4,
        "cost_usd": None,
    }


async def test_expired_lease_returns_safe_summary_and_records_event() -> None:
    repository: Final = RecordingRepository()
    recorder: Final = RequestEventRecorder(repository, clock=lambda: _NOW)
    store: Final = RequestEventStateStore(MemoryStateStore(), recorder)
    account: Final = _account()
    await store.configure((account,))
    result: Final = await store.reserve(
        account=account,
        deployment_id="deployment-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="request-expired",
        estimated_tokens=0,
        ttl_seconds=0,
    )
    assert isinstance(result, ReserveSuccess)

    expired: Final = await store.sweep_expired()

    assert expired == (result.lease,)
    assert repository.records[0].event.event_type == OperationalEventType.LEASE_EXPIRED
    assert repository.records[0].event.reason_code == "lease_expired"


def test_acquire_failure_hashes_unsafe_request_id_and_excludes_sensitive_data() -> None:
    rejection: Final = AcquireCandidateRejection(
        account_id="channel-account",
        deployment_id="deployment-a",
        stage=AcquireRejectionStage.RESERVATION,
        reason_code="capacity",
        scope=AcquireRejectionScope.CHANNEL,
        source=AcquireRejectionSource.CAPACITY,
    )

    record: Final = build_request_acquire_failed_record(
        channel_id=_CHANNEL_ID,
        model_id="model-a",
        request_id="unsafe request/id?with=secret",
        rejection=rejection,
        occurred_at=_NOW,
    )
    serialized: Final = record.model_dump_json()

    assert record.event.request_id is not None and record.event.request_id.startswith("sha256:")
    assert "unsafe request" not in serialized
    assert "api_key" not in serialized
    assert record.event.safe_details.model_dump(mode="json") == {
        "kind": "request_acquire_failed",
        "rejection_stage": "reservation",
        "rejection_scope": "channel",
        "rejection_source": "capacity",
        "retry_at": None,
    }


async def test_scheduler_records_success_and_candidate_failure() -> None:
    repository: Final = RecordingRepository()
    recorder: Final = RequestEventRecorder(repository, clock=lambda: _NOW)
    store: Final = MemoryStateStore()
    scheduler: Final = Scheduler(
        config=PoolConfig(accounts=(_account().model_copy(update={"max_concurrency": 1}),)),
        store=store,
        lease_ttl_seconds=60,
        request_events=recorder,
    )
    await scheduler.initialize()

    acquired: Final = await scheduler.acquire(AcquireRequest(request_id="request-1", model="model-a"))
    unavailable: Final = await scheduler.acquire(AcquireRequest(request_id="request-2", model="model-a"))

    assert isinstance(acquired, AcquireSuccess)
    assert isinstance(unavailable, AcquireUnavailable)
    assert tuple(record.event.event_type for record in repository.records) == (
        OperationalEventType.REQUEST_ACQUIRED,
        OperationalEventType.REQUEST_ACQUIRE_FAILED,
    )
    assert repository.records[1].event.reason_code == "capacity"


async def test_duplicate_acquire_builds_the_same_idempotent_event() -> None:
    repository: Final = RecordingRepository()
    recorder: Final = RequestEventRecorder(repository, clock=lambda: _NOW)
    store: Final = MemoryStateStore()
    scheduler: Final = Scheduler(
        config=PoolConfig(accounts=(_account(),)),
        store=store,
        lease_ttl_seconds=60,
        request_events=recorder,
    )
    await scheduler.initialize()
    request: Final = AcquireRequest(request_id="request-idempotent", model="model-a")

    first: Final = await scheduler.acquire(request)
    repeated: Final = await scheduler.acquire(request)

    assert isinstance(first, AcquireSuccess)
    assert isinstance(repeated, AcquireSuccess)
    assert repository.records[0] == repository.records[1]
