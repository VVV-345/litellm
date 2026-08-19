"""验证主动健康探测的定向调用、资格约束、恢复行为和安全失败分类。"""

from __future__ import annotations

from typing import Final
from uuid import UUID

import httpx
from account_pool.health.probe import ActiveHealthProbeService, HealthProbeRequest, HealthProbeStatus
from account_pool.models import (
    AccountConfig,
    AcquireRequest,
    AcquireSuccess,
    AcquireUnavailable,
    DeploymentConfig,
    PoolConfig,
    QuotaConfig,
    ReserveRejected,
    ReserveSuccess,
    SettleRequest,
)
from account_pool.scheduler import Scheduler
from account_pool.store import MemoryStateStore
from pydantic import TypeAdapter

_CHANNEL_ID: Final = UUID("80000000-0000-0000-0000-000000000001")
_JSON_OBJECT: Final = TypeAdapter(dict[str, object])


def _account(*, enabled: bool = True, total_quota: float | None = None) -> AccountConfig:
    return AccountConfig(
        id="channel-a",
        channel_id=_CHANNEL_ID,
        display_name="Channel A",
        provider="test",
        base_url_display="https://provider.example/v1",
        enabled=enabled,
        max_concurrency=2,
        quotas=QuotaConfig(total=total_quota),
        deployments=(
            DeploymentConfig(
                public_model="model-a",
                litellm_model_id="deployment-a",
                provider_model="openai/model-a",
            ),
        ),
    )


async def _runtime(account: AccountConfig) -> tuple[Scheduler, MemoryStateStore]:
    store: Final = MemoryStateStore()
    scheduler: Final = Scheduler(PoolConfig(accounts=(account,)), store, lease_ttl_seconds=60)
    await scheduler.initialize()
    return scheduler, store


def _service(
    scheduler: Scheduler,
    store: MemoryStateStore,
    client: httpx.AsyncClient,
    admin_key: str | None = "admin-secret",
) -> ActiveHealthProbeService:
    return ActiveHealthProbeService(
        accounts=scheduler,
        store=store,
        client=client,
        litellm_url="http://litellm.internal",
        admin_key=admin_key,
        lease_ttl_seconds=60,
    )


async def test_successful_probe_targets_deployment_and_marks_channel_healthy() -> None:
    scheduler, store = await _runtime(_account())
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "success", "result": {"ignored": "provider-secret"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await _service(scheduler, store, client).probe_channel(
            _CHANNEL_ID,
            HealthProbeRequest(deployment_id="deployment-a"),
        )

    payload: Final = _JSON_OBJECT.validate_json(requests[0].content)
    model_info: Final = _JSON_OBJECT.validate_python(payload["model_info"])
    snapshot: Final = (await store.snapshots())[0]
    assert result.status == HealthProbeStatus.SUCCEEDED
    assert result.deployment_id == "deployment-a"
    assert result.reason_code is None
    assert requests[0].url.path == "/health/test_connection"
    assert requests[0].headers["authorization"] == "Bearer admin-secret"
    assert model_info == {"id": "deployment-a"}
    assert "provider-secret" not in result.model_dump_json()
    assert snapshot.health == "healthy"
    assert snapshot.inflight == 0


async def test_manual_probe_can_recover_an_active_health_exclusion() -> None:
    account: Final = _account()
    scheduler, store = await _runtime(account)
    initial: Final = await scheduler.acquire(AcquireRequest(request_id="initial", model="model-a"))
    assert isinstance(initial, AcquireSuccess)
    assert await store.settle(SettleRequest(lease_id=initial.lease.lease_id, success=False, status_code=401))
    assert await store.release(initial.lease.lease_id)
    blocked: Final = await scheduler.acquire(AcquireRequest(request_id="blocked", model="model-a"))
    assert isinstance(blocked, AcquireUnavailable)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"status": "success", "result": {}}))
    ) as client:
        result: Final = await _service(scheduler, store, client).probe_channel(
            _CHANNEL_ID,
            HealthProbeRequest(),
        )
    recovered: Final = await scheduler.acquire(AcquireRequest(request_id="recovered", model="model-a"))

    assert result.status == HealthProbeStatus.SUCCEEDED
    assert isinstance(recovered, AcquireSuccess)


async def test_probe_does_not_bypass_exhausted_quota() -> None:
    scheduler, store = await _runtime(_account(total_quota=0))
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "success"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await _service(scheduler, store, client).probe_channel(
            _CHANNEL_ID,
            HealthProbeRequest(),
        )

    assert result.status == HealthProbeStatus.SKIPPED
    assert result.reason_code == "total_quota"
    assert requests == []


async def test_litellm_admin_auth_failure_does_not_mark_provider_unhealthy() -> None:
    scheduler, store = await _runtime(_account())
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(401, json={"detail": "invalid admin key"}))
    ) as client:
        result: Final = await _service(scheduler, store, client).probe_channel(
            _CHANNEL_ID,
            HealthProbeRequest(),
        )

    snapshot: Final = (await store.snapshots())[0]
    assert result.status == HealthProbeStatus.FAILED
    assert result.reason_code == "litellm_admin_auth_failed"
    assert snapshot.health == "unknown"
    assert snapshot.inflight == 0


async def test_probe_reservation_has_single_inflight_owner() -> None:
    account: Final = _account()
    _, store = await _runtime(account)
    first: Final = await store.reserve(
        account=account,
        deployment_id="deployment-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="probe-a",
        estimated_tokens=1,
        ttl_seconds=60,
        probe=True,
    )
    second: Final = await store.reserve(
        account=account,
        deployment_id="deployment-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="probe-b",
        estimated_tokens=1,
        ttl_seconds=60,
        probe=True,
    )

    assert isinstance(first, ReserveSuccess)
    assert isinstance(second, ReserveRejected)
    assert second.reason == "half_open_probe_inflight"


async def test_due_probe_checks_unknown_channel_as_initial_verification() -> None:
    scheduler, store = await _runtime(_account())
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"status": "success", "result": {}}))
    ) as client:
        results: Final = await _service(scheduler, store, client).probe_due()

    assert len(results) == 1
    assert results[0].status == HealthProbeStatus.SUCCEEDED
    assert results[0].trigger == "initial"


async def test_due_probe_rechecks_half_open_deployment() -> None:
    scheduler, store = await _runtime(_account())
    acquired: Final = await scheduler.acquire(AcquireRequest(request_id="limited", model="model-a"))
    assert isinstance(acquired, AcquireSuccess)
    assert await store.settle(
        SettleRequest(
            lease_id=acquired.lease.lease_id,
            success=False,
            status_code=429,
            retry_after_seconds=0,
        )
    )
    assert await store.release(acquired.lease.lease_id)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"status": "success", "result": {}}))
    ) as client:
        results: Final = await _service(scheduler, store, client).probe_due()

    assert len(results) == 1
    assert results[0].status == HealthProbeStatus.SUCCEEDED
    assert results[0].trigger == "half_open"


async def test_manual_probe_does_not_bypass_active_rate_limit() -> None:
    scheduler, store = await _runtime(_account())
    acquired: Final = await scheduler.acquire(AcquireRequest(request_id="limited", model="model-a"))
    assert isinstance(acquired, AcquireSuccess)
    assert await store.settle(
        SettleRequest(
            lease_id=acquired.lease.lease_id,
            success=False,
            status_code=429,
            retry_after_seconds=120,
        )
    )
    assert await store.release(acquired.lease.lease_id)
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "success"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result: Final = await _service(scheduler, store, client).probe_channel(
            _CHANNEL_ID,
            HealthProbeRequest(),
        )

    assert result.status == HealthProbeStatus.SKIPPED
    assert result.reason_code == "rate_limit_unknown"
    assert requests == []


async def test_due_probe_retries_degraded_initial_verification() -> None:
    scheduler, store = await _runtime(_account())
    responses = iter(
        (
            httpx.Response(200, json={"status": "error", "result": {}}),
            httpx.Response(200, json={"status": "success", "result": {}}),
        )
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: next(responses))) as client:
        service: Final = _service(scheduler, store, client)
        failed: Final = await service.probe_due()
        degraded: Final = (await store.snapshots())[0]
        recovered: Final = await service.probe_due()

    assert failed[0].status == HealthProbeStatus.FAILED
    assert degraded.health == "degraded"
    assert recovered[0].status == HealthProbeStatus.SUCCEEDED
    assert (await store.snapshots())[0].health == "healthy"
