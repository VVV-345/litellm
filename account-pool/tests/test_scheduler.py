"""验证号池调度策略、并发租约和额度结算行为。"""

from __future__ import annotations

from typing import Final

import pytest
from account_pool.models import (
    AccountConfig,
    AcquireRequest,
    AcquireSuccess,
    AcquireUnavailable,
    DeploymentConfig,
    ModelPolicy,
    PoolConfig,
    QuotaConfig,
    SettleRequest,
    Strategy,
)
from account_pool.scheduler import Scheduler
from account_pool.store import MemoryStateStore


def account(
    account_id: str,
    max_concurrency: int,
    weight: int = 1,
    models: tuple[str, ...] = ("model-a",),
) -> AccountConfig:
    return AccountConfig(
        id=account_id,
        display_name=account_id,
        provider="test",
        base_url_display="https://example.test",
        max_concurrency=max_concurrency,
        weight=weight,
        quotas=QuotaConfig(total=10_000, five_hour=5_000, weekly=8_000),
        deployments=tuple(
            DeploymentConfig(public_model=model, litellm_model_id=f"{account_id}-{model}") for model in models
        ),
    )


async def initialized_scheduler(config: PoolConfig) -> tuple[Scheduler, MemoryStateStore]:
    store: Final = MemoryStateStore()
    scheduler: Final = Scheduler(config=config, store=store, lease_ttl_seconds=60)
    await scheduler.initialize()
    return scheduler, store


@pytest.mark.asyncio
async def test_acquire_is_idempotent_and_capacity_is_never_exceeded() -> None:
    scheduler, store = await initialized_scheduler(PoolConfig(accounts=(account("one", max_concurrency=1),)))

    first: Final = await scheduler.acquire(AcquireRequest(request_id="request-1", model="model-a"))
    duplicate: Final = await scheduler.acquire(AcquireRequest(request_id="request-1", model="model-a"))
    blocked: Final = await scheduler.acquire(AcquireRequest(request_id="request-2", model="model-a"))

    assert isinstance(first, AcquireSuccess)
    assert isinstance(duplicate, AcquireSuccess)
    assert duplicate.lease.lease_id == first.lease.lease_id
    assert isinstance(blocked, AcquireUnavailable)
    assert blocked.reasons == ("one:capacity",)
    assert (await store.snapshots())[0].inflight == 1


@pytest.mark.asyncio
async def test_models_on_the_same_account_share_concurrency() -> None:
    scheduler, store = await initialized_scheduler(
        PoolConfig(accounts=(account("shared", max_concurrency=1, models=("model-a", "model-b")),))
    )

    first: Final = await scheduler.acquire(AcquireRequest(request_id="request-a", model="model-a"))
    second: Final = await scheduler.acquire(AcquireRequest(request_id="request-b", model="model-b"))

    assert isinstance(first, AcquireSuccess)
    assert isinstance(second, AcquireUnavailable)
    assert await store.release(first.lease.lease_id)
    retry: Final = await scheduler.acquire(AcquireRequest(request_id="request-b", model="model-b"))
    assert isinstance(retry, AcquireSuccess)


@pytest.mark.asyncio
async def test_settlement_updates_quota_and_429_cools_account() -> None:
    scheduler, store = await initialized_scheduler(PoolConfig(accounts=(account("one", max_concurrency=2),)))
    acquired: Final = await scheduler.acquire(AcquireRequest(request_id="request-1", model="model-a"))
    assert isinstance(acquired, AcquireSuccess)

    assert await store.settle(
        SettleRequest(
            lease_id=acquired.lease.lease_id,
            success=False,
            status_code=429,
            input_tokens=120,
            output_tokens=30,
            error_type="provider_rate_limit",
        )
    )
    assert await store.release(acquired.lease.lease_id)

    snapshot: Final = (await store.snapshots())[0]
    assert snapshot.health == "cooldown"
    assert snapshot.quota.total == 9_850
    blocked: Final = await scheduler.acquire(AcquireRequest(request_id="request-2", model="model-a"))
    assert isinstance(blocked, AcquireUnavailable)
    assert blocked.reasons == ("one:cooldown",)


@pytest.mark.asyncio
async def test_weighted_round_robin_honors_configured_weight() -> None:
    config: Final = PoolConfig(
        accounts=(account("heavy", 10, weight=3), account("light", 10, weight=1)),
        policies=(ModelPolicy(model="model-a", strategy=Strategy.WEIGHTED_ROUND_ROBIN),),
    )
    scheduler, store = await initialized_scheduler(config)
    selected: list[str] = []

    for index in range(8):
        result = await scheduler.acquire(AcquireRequest(request_id=f"request-{index}", model="model-a"))
        assert isinstance(result, AcquireSuccess)
        selected.append(result.lease.account_id)
        assert await store.release(result.lease.lease_id)

    assert selected.count("heavy") == 6
    assert selected.count("light") == 2


@pytest.mark.asyncio
async def test_reconfigure_preserves_usage_unless_quota_limits_change() -> None:
    original: Final = account("one", max_concurrency=2)
    scheduler, store = await initialized_scheduler(PoolConfig(accounts=(original,)))
    acquired: Final = await scheduler.acquire(AcquireRequest(request_id="request-1", model="model-a"))
    assert isinstance(acquired, AcquireSuccess)
    assert await store.settle(
        SettleRequest(lease_id=acquired.lease.lease_id, success=True, input_tokens=100, output_tokens=50)
    )
    assert await store.release(acquired.lease.lease_id)

    await scheduler.reconfigure(
        PoolConfig(
            accounts=(original,),
            policies=(ModelPolicy(model="model-a", strategy=Strategy.PRIORITY),),
        )
    )
    assert (await store.snapshots())[0].quota.total == 9_850

    updated: Final = original.model_copy(update={"quotas": QuotaConfig(total=20_000, five_hour=7_000, weekly=15_000)})
    await scheduler.reconfigure(PoolConfig(accounts=(updated,)))
    snapshot: Final = (await store.snapshots())[0]
    assert snapshot.quota.total == 20_000
    assert snapshot.quota.five_hour == 7_000
    assert snapshot.quota.weekly == 15_000
