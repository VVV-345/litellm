"""验证号池调度策略、并发租约和额度结算行为。"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Final
from uuid import UUID, uuid4

import pytest
from account_pool.models import (
    AccountConfig,
    AcquireRequest,
    AcquireSuccess,
    AcquireUnavailable,
    CostEvidenceKind,
    DeploymentConfig,
    DeploymentCostEvidence,
    ModelPolicy,
    PoolConfig,
    QuotaConfig,
    QuotaWindowConfig,
    ReserveSuccess,
    RuntimeBillingMode,
    RuntimeQuotaKind,
    RuntimeQuotaScope,
    RuntimeQuotaWindowType,
    SettleRequest,
    Strategy,
)
from account_pool.quota.backend import QuotaBackendWindowState
from account_pool.quota.runtime import RuntimeQuotaWindow
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


def quota_window(
    window_id: str,
    remaining: str,
    scope: RuntimeQuotaScope = RuntimeQuotaScope.CHANNEL,
    subject_id: str | None = None,
    kind: RuntimeQuotaKind = RuntimeQuotaKind.TOKENS,
    reason_code: str = "five_hour_exhausted",
) -> QuotaWindowConfig:
    return QuotaWindowConfig(
        window_id=window_id,
        scope=scope,
        subject_id=subject_id,
        kind=kind,
        window_type=RuntimeQuotaWindowType.ROLLING,
        duration_seconds=18_000,
        limit=Decimal("100"),
        remaining=Decimal(remaining),
        observed_at=time.time(),
        source="provider-api",
        reason_code=reason_code,
    )


async def initialized_scheduler(config: PoolConfig) -> tuple[Scheduler, MemoryStateStore]:
    store: Final = MemoryStateStore()
    scheduler: Final = Scheduler(config=config, store=store, lease_ttl_seconds=60)
    await scheduler.initialize()
    return scheduler, store


@pytest.mark.asyncio
async def test_memory_restore_rejects_windows_with_outstanding_reservations() -> None:
    config: Final = QuotaWindowConfig(
        window_id="window-a",
        scope=RuntimeQuotaScope.CHANNEL,
        subject_id=None,
        kind=RuntimeQuotaKind.TOKENS,
        window_type=RuntimeQuotaWindowType.ROLLING,
        duration_seconds=18_000,
        limit=Decimal("100"),
        remaining=Decimal("80"),
        observed_at=time.time(),
        source="provider-api",
        reason_code="five_hour_exhausted",
    )
    account_config: Final = AccountConfig(
        id="channel-a",
        display_name="Channel A",
        provider="test",
        base_url_display="https://example.test",
        max_concurrency=2,
        deployments=(DeploymentConfig(public_model="model-a", litellm_model_id="deployment-a"),),
        quota_windows=(config,),
    )
    store: Final = MemoryStateStore()
    await store.configure((account_config,))

    def restored_state(reserved: Decimal) -> QuotaBackendWindowState:
        return QuotaBackendWindowState(
            account_id="channel-a",
            window=RuntimeQuotaWindow(config=config, remaining=Decimal("80"), retry_at=None, reserved=reserved),
        )

    generation: Final = UUID("30000000-0000-0000-0000-000000000003")
    assert await store.restore_quota_backend(generation, (restored_state(Decimal("0")),)) is True
    assert await store.read_quota_generation() == generation
    assert await store.restore_quota_backend(uuid4(), (restored_state(Decimal("5")),)) is False
    assert await store.read_quota_generation() == generation


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
    assert blocked.code == "no_available_route"
    assert blocked.reason_codes == ("capacity",)
    assert blocked.candidates[0].model_dump(mode="json") == {
        "account_id": "one",
        "deployment_id": "one-model-a",
        "binding_id": None,
        "billing_route_id": None,
        "stage": "eligibility",
        "reason_code": "capacity",
        "scope": "channel",
        "source": "capacity",
        "state": "active",
        "retry_at": None,
    }
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
async def test_settlement_updates_quota_and_429_restricts_only_the_deployment() -> None:
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
    assert snapshot.health == "unknown"
    assert snapshot.reason_code is None
    assert snapshot.quota.total == 9_850
    route: Final = (await scheduler.route_table("model-a"))[0]
    assert route.exclusion_scope == "deployment"
    assert route.reason_code == "rate_limited"
    assert route.available is False
    blocked: Final = await scheduler.acquire(AcquireRequest(request_id="request-2", model="model-a"))
    assert isinstance(blocked, AcquireUnavailable)
    assert blocked.reasons == ("one:rate_limited",)
    assert blocked.reason_codes == ("rate_limited",)
    assert blocked.candidates[0].scope == "deployment"
    assert blocked.candidates[0].source == "restriction"
    assert blocked.candidates[0].stage == "eligibility"
    assert blocked.candidates[0].retry_at is not None
    assert blocked.retry_at == blocked.candidates[0].retry_at


@pytest.mark.asyncio
async def test_retry_after_controls_cooldown_without_marking_quota_exhausted() -> None:
    scheduler, store = await initialized_scheduler(PoolConfig(accounts=(account("one", max_concurrency=2),)))
    acquired: Final = await scheduler.acquire(AcquireRequest(request_id="request-1", model="model-a"))
    assert isinstance(acquired, AcquireSuccess)
    before: Final = time.time()

    assert await store.settle(
        SettleRequest(
            lease_id=acquired.lease.lease_id,
            success=False,
            status_code=429,
            provider_error_code="concurrency_limit_exceeded",
            retry_after_seconds=120,
        )
    )

    snapshot: Final = (await store.snapshots())[0]
    route: Final = (await scheduler.route_table("model-a"))[0]
    assert route.reason_code == "concurrency_limited"
    assert route.retry_at is not None
    assert before + 120 <= route.retry_at <= time.time() + 120
    assert snapshot.cooldown_until is None
    assert snapshot.quota.five_hour == 5_000
    assert snapshot.quota.weekly == 8_000


@pytest.mark.asyncio
async def test_expired_retry_enters_half_open_and_success_clears_the_restriction() -> None:
    scheduler, store = await initialized_scheduler(PoolConfig(accounts=(account("one", max_concurrency=2),)))
    first: Final = await scheduler.acquire(AcquireRequest(request_id="request-1", model="model-a"))
    assert isinstance(first, AcquireSuccess)
    assert await store.settle(
        SettleRequest(
            lease_id=first.lease.lease_id,
            success=False,
            status_code=429,
            retry_after_seconds=0,
        )
    )
    assert await store.release(first.lease.lease_id)

    half_open: Final = (await scheduler.route_table("model-a"))[0]
    second: Final = await scheduler.acquire(AcquireRequest(request_id="request-2", model="model-a"))
    concurrent: Final = await scheduler.acquire(AcquireRequest(request_id="request-3", model="model-a"))

    assert half_open.exclusion_state == "half_open"
    assert half_open.available is True
    assert isinstance(second, AcquireSuccess)
    assert isinstance(concurrent, AcquireUnavailable)
    assert concurrent.reasons == ("one:half_open_probe_inflight",)
    assert concurrent.candidates[0].source == "capacity"
    assert concurrent.candidates[0].state == "half_open"
    assert concurrent.candidates[0].stage == "reservation"
    assert await store.settle(SettleRequest(lease_id=second.lease.lease_id, success=True))
    assert await store.release(second.lease.lease_id)
    recovered: Final = (await scheduler.route_table("model-a"))[0]
    assert recovered.exclusion_state is None
    assert recovered.reason_code is None


@pytest.mark.asyncio
async def test_released_half_open_probe_allows_another_probe() -> None:
    scheduler, store = await initialized_scheduler(PoolConfig(accounts=(account("one", max_concurrency=2),)))
    first: Final = await scheduler.acquire(AcquireRequest(request_id="request-1", model="model-a"))
    assert isinstance(first, AcquireSuccess)
    assert await store.settle(
        SettleRequest(lease_id=first.lease.lease_id, success=False, status_code=429, retry_after_seconds=0)
    )
    assert await store.release(first.lease.lease_id)
    abandoned: Final = await scheduler.acquire(AcquireRequest(request_id="request-2", model="model-a"))
    assert isinstance(abandoned, AcquireSuccess)

    assert await store.release(abandoned.lease.lease_id)
    replacement: Final = await scheduler.acquire(AcquireRequest(request_id="request-3", model="model-a"))
    assert isinstance(replacement, AcquireSuccess)


@pytest.mark.asyncio
async def test_failed_half_open_probe_reactivates_the_restriction() -> None:
    scheduler, store = await initialized_scheduler(PoolConfig(accounts=(account("one", max_concurrency=2),)))
    first: Final = await scheduler.acquire(AcquireRequest(request_id="request-1", model="model-a"))
    assert isinstance(first, AcquireSuccess)
    assert await store.settle(
        SettleRequest(lease_id=first.lease.lease_id, success=False, status_code=429, retry_after_seconds=0)
    )
    assert await store.release(first.lease.lease_id)
    probe: Final = await scheduler.acquire(AcquireRequest(request_id="request-2", model="model-a"))
    assert isinstance(probe, AcquireSuccess)
    assert await store.settle(
        SettleRequest(lease_id=probe.lease.lease_id, success=False, status_code=429, retry_after_seconds=120)
    )
    assert await store.release(probe.lease.lease_id)

    route: Final = (await scheduler.route_table("model-a"))[0]
    blocked: Final = await scheduler.acquire(AcquireRequest(request_id="request-3", model="model-a"))

    assert route.exclusion_state == "active"
    assert route.reason_code == "rate_limit_unknown"
    assert isinstance(blocked, AcquireUnavailable)
    assert blocked.reasons == ("one:rate_limit_unknown",)


@pytest.mark.asyncio
async def test_half_open_probe_contention_falls_back_to_another_channel() -> None:
    primary: Final = account("primary", max_concurrency=2).model_copy(update={"priority": 100})
    fallback: Final = account("fallback", max_concurrency=2).model_copy(update={"priority": 0})
    scheduler, store = await initialized_scheduler(
        PoolConfig(
            accounts=(primary, fallback),
            policies=(ModelPolicy(model="model-a", strategy=Strategy.PRIORITY),),
        )
    )
    first: Final = await scheduler.acquire(AcquireRequest(request_id="request-1", model="model-a"))
    assert isinstance(first, AcquireSuccess)
    assert first.lease.account_id == "primary"
    assert await store.settle(
        SettleRequest(lease_id=first.lease.lease_id, success=False, status_code=429, retry_after_seconds=0)
    )
    assert await store.release(first.lease.lease_id)
    probe: Final = await scheduler.acquire(AcquireRequest(request_id="request-2", model="model-a"))
    assert isinstance(probe, AcquireSuccess)
    assert probe.lease.account_id == "primary"

    concurrent: Final = await scheduler.acquire(AcquireRequest(request_id="request-3", model="model-a"))
    assert isinstance(concurrent, AcquireSuccess)
    assert concurrent.lease.account_id == "fallback"


@pytest.mark.asyncio
async def test_model_not_found_does_not_degrade_entire_account() -> None:
    scheduler, store = await initialized_scheduler(PoolConfig(accounts=(account("one", max_concurrency=2),)))
    acquired: Final = await scheduler.acquire(AcquireRequest(request_id="request-1", model="model-a"))
    assert isinstance(acquired, AcquireSuccess)

    assert await store.settle(
        SettleRequest(
            lease_id=acquired.lease.lease_id,
            success=False,
            status_code=404,
        )
    )

    snapshot: Final = (await store.snapshots())[0]
    assert snapshot.health == "unknown"
    assert snapshot.reason_code is None
    assert snapshot.cooldown_until is None
    route: Final = (await scheduler.route_table("model-a"))[0]
    assert route.reason_code == "model_not_found"
    assert route.exclusion_scope == "deployment"
    assert route.available is False


@pytest.mark.asyncio
async def test_deployment_failure_does_not_block_other_models_on_the_account() -> None:
    scheduler, store = await initialized_scheduler(
        PoolConfig(accounts=(account("shared", max_concurrency=2, models=("model-a", "model-b")),))
    )
    acquired: Final = await scheduler.acquire(AcquireRequest(request_id="request-a", model="model-a"))
    assert isinstance(acquired, AcquireSuccess)
    assert await store.settle(SettleRequest(lease_id=acquired.lease.lease_id, success=False, status_code=404))
    assert await store.release(acquired.lease.lease_id)

    blocked: Final = await scheduler.acquire(AcquireRequest(request_id="request-a-2", model="model-a"))
    available: Final = await scheduler.acquire(AcquireRequest(request_id="request-b", model="model-b"))

    assert isinstance(blocked, AcquireUnavailable)
    assert blocked.reasons == ("shared:model_not_found",)
    assert isinstance(available, AcquireSuccess)


@pytest.mark.asyncio
async def test_invalid_credential_excludes_the_channel_without_changing_admin_state() -> None:
    scheduler, store = await initialized_scheduler(
        PoolConfig(accounts=(account("shared", max_concurrency=2, models=("model-a", "model-b")),))
    )
    acquired: Final = await scheduler.acquire(AcquireRequest(request_id="request-a", model="model-a"))
    assert isinstance(acquired, AcquireSuccess)
    assert await store.settle(SettleRequest(lease_id=acquired.lease.lease_id, success=False, status_code=401))
    assert await store.release(acquired.lease.lease_id)

    snapshot: Final = (await store.snapshots())[0]
    blocked: Final = await scheduler.acquire(AcquireRequest(request_id="request-b", model="model-b"))

    assert snapshot.enabled is True
    assert snapshot.health == "unhealthy"
    assert isinstance(blocked, AcquireUnavailable)
    assert blocked.reasons == ("shared:credential_invalid",)


@pytest.mark.asyncio
async def test_repeated_provider_failures_create_a_channel_scope_exclusion() -> None:
    scheduler, store = await initialized_scheduler(
        PoolConfig(accounts=(account("shared", max_concurrency=2, models=("model-a", "model-b")),))
    )
    for index in range(3):
        acquired = await scheduler.acquire(AcquireRequest(request_id=f"request-{index}", model="model-a"))
        assert isinstance(acquired, AcquireSuccess)
        assert await store.settle(SettleRequest(lease_id=acquired.lease.lease_id, success=False, status_code=503))
        assert await store.release(acquired.lease.lease_id)

    route: Final = (await scheduler.route_table("model-b"))[0]
    blocked: Final = await scheduler.acquire(AcquireRequest(request_id="request-b", model="model-b"))

    assert route.exclusion_scope == "channel"
    assert route.reason_code == "upstream_unavailable"
    assert isinstance(blocked, AcquireUnavailable)
    assert blocked.reasons == ("shared:upstream_unavailable",)


@pytest.mark.asyncio
async def test_billing_route_balance_signal_falls_back_to_sibling_route() -> None:
    configured: Final = account("shared", max_concurrency=2).model_copy(
        update={
            "deployments": (
                DeploymentConfig(
                    public_model="model-a",
                    litellm_model_id="deployment-a",
                    billing_route_id="route-a",
                ),
                DeploymentConfig(
                    public_model="model-a",
                    litellm_model_id="deployment-b",
                    billing_route_id="route-b",
                ),
            )
        }
    )
    scheduler, store = await initialized_scheduler(PoolConfig(accounts=(configured,)))
    first: Final = await scheduler.acquire(AcquireRequest(request_id="request-a", model="model-a"))
    assert isinstance(first, AcquireSuccess)
    assert first.lease.billing_route_id == "route-a"
    assert await store.settle(
        SettleRequest(
            lease_id=first.lease.lease_id,
            success=False,
            status_code=429,
            provider_error_code="billing_hard_limit_reached",
        )
    )
    assert await store.release(first.lease.lease_id)

    second: Final = await scheduler.acquire(AcquireRequest(request_id="request-b", model="model-a"))
    exclusions: Final = await store.eligibility_exclusions()

    assert exclusions[0].scope == "billing_route"
    assert exclusions[0].billing_route_id == "route-a"
    assert isinstance(second, AcquireSuccess)
    assert second.lease.billing_route_id == "route-b"


@pytest.mark.asyncio
async def test_priority_strategy_lists_and_selects_available_fallback_first() -> None:
    primary: Final = account("primary", max_concurrency=1).model_copy(update={"priority": 100})
    fallback: Final = account("fallback", max_concurrency=1).model_copy(update={"priority": 0})
    scheduler, store = await initialized_scheduler(
        PoolConfig(
            accounts=(primary, fallback),
            policies=(ModelPolicy(model="model-a", strategy=Strategy.PRIORITY),),
        )
    )
    first: Final = await scheduler.acquire(AcquireRequest(request_id="request-a", model="model-a"))
    assert isinstance(first, AcquireSuccess)
    assert first.lease.account_id == "primary"
    assert await store.settle(SettleRequest(lease_id=first.lease.lease_id, success=False, status_code=404))
    assert await store.release(first.lease.lease_id)

    routes: Final = await scheduler.route_table("model-a")
    second: Final = await scheduler.acquire(AcquireRequest(request_id="request-b", model="model-a"))

    assert routes[0].account_id == "fallback"
    assert routes[0].available is True
    assert routes[1].account_id == "primary"
    assert routes[1].reason_code == "model_not_found"
    assert isinstance(second, AcquireSuccess)
    assert second.lease.account_id == "fallback"


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
async def test_lowest_effective_cost_selects_the_cheapest_projected_candidate() -> None:
    expensive: Final = account("expensive", max_concurrency=2).model_copy(
        update={
            "deployments": (
                DeploymentConfig(
                    public_model="model-a",
                    litellm_model_id="expensive-model-a",
                    cost_evidence=DeploymentCostEvidence(
                        kind=CostEvidenceKind.NORMALIZED_PER_MILLION_TOKENS,
                        currency="USD",
                        unit="million_tokens",
                        input_price=Decimal("4"),
                        output_price=Decimal("12"),
                        effective_cost=Decimal("16"),
                        billing_mode=RuntimeBillingMode.PROVIDER_DECIDED,
                    ),
                ),
            )
        }
    )
    cheap: Final = account("cheap", max_concurrency=2).model_copy(
        update={
            "deployments": (
                DeploymentConfig(
                    public_model="model-a",
                    litellm_model_id="cheap-model-a",
                    cost_evidence=DeploymentCostEvidence(
                        kind=CostEvidenceKind.NORMALIZED_PER_MILLION_TOKENS,
                        currency="USD",
                        unit="million_tokens",
                        input_price=Decimal("1"),
                        output_price=Decimal("3"),
                        effective_cost=Decimal("4"),
                        billing_mode=RuntimeBillingMode.PROVIDER_DECIDED,
                    ),
                ),
            )
        }
    )
    scheduler, _ = await initialized_scheduler(
        PoolConfig(
            accounts=(expensive, cheap),
            policies=(ModelPolicy(model="model-a", strategy=Strategy.LOWEST_EFFECTIVE_COST),),
        )
    )

    routes: Final = await scheduler.route_table("model-a")
    acquired: Final = await scheduler.acquire(AcquireRequest(request_id="request-cost", model="model-a"))

    assert tuple(route.account_id for route in routes) == ("cheap", "expensive")
    assert routes[0].effective_cost == Decimal("4")
    assert routes[0].cost_evidence is not None
    assert routes[0].cost_evidence.billing_mode == "provider_decided"
    assert isinstance(acquired, AcquireSuccess)
    assert acquired.lease.account_id == "cheap"


@pytest.mark.asyncio
async def test_lowest_latency_uses_successful_ewma_for_route_table_and_acquire() -> None:
    slow: Final = account("slow", max_concurrency=2)
    fast: Final = account("fast", max_concurrency=2)
    scheduler, store = await initialized_scheduler(
        PoolConfig(
            accounts=(slow, fast),
            policies=(ModelPolicy(model="model-a", strategy=Strategy.LOWEST_LATENCY),),
        )
    )
    slow_sample: Final = await store.reserve(
        account=slow,
        deployment_id="slow-model-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="slow-sample",
        estimated_tokens=0,
        ttl_seconds=60,
    )
    fast_sample: Final = await store.reserve(
        account=fast,
        deployment_id="fast-model-a",
        billing_route_id=None,
        public_model="model-a",
        request_id="fast-sample",
        estimated_tokens=0,
        ttl_seconds=60,
    )
    assert isinstance(slow_sample, ReserveSuccess)
    assert isinstance(fast_sample, ReserveSuccess)
    assert await store.settle(SettleRequest(lease_id=slow_sample.lease.lease_id, success=True, latency_ms=300))
    assert await store.settle(SettleRequest(lease_id=fast_sample.lease.lease_id, success=True, latency_ms=50))
    assert await store.release(slow_sample.lease.lease_id)
    assert await store.release(fast_sample.lease.lease_id)

    routes: Final = await scheduler.route_table("model-a")
    acquired: Final = await scheduler.acquire(AcquireRequest(request_id="request-latency", model="model-a"))

    assert tuple(route.account_id for route in routes) == ("fast", "slow")
    assert tuple(route.latency_ewma_ms for route in routes) == (50, 300)
    assert routes[0].sort_reason_codes == ("latency_ewma",)
    assert isinstance(acquired, AcquireSuccess)
    assert acquired.lease.account_id == "fast"


@pytest.mark.asyncio
async def test_reconfigure_preserves_latency_metrics_for_configured_deployments() -> None:
    configured: Final = account("one", max_concurrency=2)
    scheduler, store = await initialized_scheduler(
        PoolConfig(
            accounts=(configured,),
            policies=(ModelPolicy(model="model-a", strategy=Strategy.LOWEST_LATENCY),),
        )
    )
    sample: Final = await scheduler.acquire(AcquireRequest(request_id="sample", model="model-a"))
    assert isinstance(sample, AcquireSuccess)
    assert await store.settle(SettleRequest(lease_id=sample.lease.lease_id, success=True, latency_ms=125))

    await scheduler.reconfigure(
        PoolConfig(
            accounts=(configured,),
            policies=(ModelPolicy(model="model-a", strategy=Strategy.LOWEST_LATENCY),),
        )
    )

    routes: Final = await scheduler.route_table("model-a")
    assert routes[0].latency_ewma_ms == 125


@pytest.mark.asyncio
async def test_route_table_and_acquire_share_the_static_strategy_order() -> None:
    busy: Final = account("busy", 10).model_copy(update={"priority": 100})
    idle: Final = account("idle", 10).model_copy(update={"priority": 0})
    scheduler, store = await initialized_scheduler(
        PoolConfig(
            accounts=(busy, idle),
            policies=(ModelPolicy(model="model-a", strategy=Strategy.PRIORITY),),
        )
    )
    occupied: Final = await scheduler.acquire(AcquireRequest(request_id="occupied", model="model-a"))
    assert isinstance(occupied, AcquireSuccess)
    await scheduler.reconfigure(
        PoolConfig(
            accounts=(busy, idle),
            policies=(ModelPolicy(model="model-a", strategy=Strategy.LEAST_INFLIGHT),),
        )
    )

    routes: Final = await scheduler.route_table("model-a")
    acquired: Final = await scheduler.acquire(AcquireRequest(request_id="next", model="model-a"))

    assert tuple(route.account_id for route in routes) == ("idle", "busy")
    assert routes[0].position == 1
    assert routes[0].strategy == Strategy.LEAST_INFLIGHT
    assert routes[0].sort_reason_codes == ("inflight_ratio",)
    assert isinstance(acquired, AcquireSuccess)
    assert acquired.lease.account_id == routes[0].account_id
    assert await store.release(occupied.lease.lease_id)


@pytest.mark.asyncio
async def test_model_candidate_pause_remains_visible_but_is_not_acquired() -> None:
    paused_deployment: Final = DeploymentConfig(
        public_model="model-a",
        litellm_model_id="paused-deployment",
        manual_order=0,
        routing_paused=True,
    )
    paused: Final = account("paused", 10).model_copy(update={"priority": 400, "deployments": (paused_deployment,)})
    fallback: Final = account("fallback", 10)
    scheduler, _ = await initialized_scheduler(
        PoolConfig(
            accounts=(paused, fallback),
            policies=(ModelPolicy(model="model-a", strategy=Strategy.PRIORITY),),
        )
    )

    routes: Final = await scheduler.route_table("model-a")
    acquired: Final = await scheduler.acquire(AcquireRequest(request_id="request", model="model-a"))

    assert tuple(route.account_id for route in routes) == ("fallback", "paused")
    assert routes[1].unavailable_reason == "manual_pause"
    assert routes[1].routing_paused is True
    assert isinstance(acquired, AcquireSuccess)
    assert acquired.lease.account_id == "fallback"


@pytest.mark.asyncio
async def test_acquire_explains_paused_and_disabled_bindings() -> None:
    configured: Final = account("configured", 10).model_copy(
        update={
            "deployments": (
                DeploymentConfig(
                    public_model="model-a",
                    litellm_model_id="paused-deployment",
                    routing_paused=True,
                ),
                DeploymentConfig(
                    public_model="model-a",
                    litellm_model_id="disabled-deployment",
                    enabled=False,
                ),
            )
        }
    )
    scheduler, _ = await initialized_scheduler(PoolConfig(accounts=(configured,)))

    blocked: Final = await scheduler.acquire(AcquireRequest(request_id="request", model="model-a"))

    assert isinstance(blocked, AcquireUnavailable)
    assert blocked.reason_codes == ("deployment_disabled", "manual_pause")
    assert tuple(rejection.stage for rejection in blocked.candidates) == ("configuration", "configuration")
    assert tuple(rejection.source for rejection in blocked.candidates) == ("administrative", "administrative")
    assert tuple(rejection.scope for rejection in blocked.candidates) == ("deployment", "deployment")
    assert blocked.reasons == (
        "configured:deployment_disabled",
        "configured:manual_pause",
    )


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


@pytest.mark.asyncio
async def test_exhausted_channel_window_falls_back_to_next_account() -> None:
    exhausted: Final = account("exhausted", max_concurrency=2).model_copy(
        update={"priority": 100, "quota_windows": (quota_window("exhausted-window", "0"),)}
    )
    fallback: Final = account("fallback", max_concurrency=2).model_copy(update={"priority": 0})
    scheduler, _ = await initialized_scheduler(
        PoolConfig(
            accounts=(exhausted, fallback),
            policies=(ModelPolicy(model="model-a", strategy=Strategy.PRIORITY),),
        )
    )

    acquired: Final = await scheduler.acquire(AcquireRequest(request_id="request-a", model="model-a"))
    routes: Final = await scheduler.route_table("model-a")

    assert isinstance(acquired, AcquireSuccess)
    assert acquired.lease.account_id == "fallback"
    assert routes[0].account_id == "fallback"
    assert routes[1].reason_code == "five_hour_exhausted"
    assert routes[1].exclusion_scope == "channel"


@pytest.mark.asyncio
async def test_model_window_does_not_block_sibling_model() -> None:
    configured: Final = account("shared", max_concurrency=2, models=("model-a", "model-b")).model_copy(
        update={
            "quota_windows": (
                quota_window(
                    "model-window",
                    "0",
                    scope=RuntimeQuotaScope.MODEL,
                    subject_id="model-a",
                    reason_code="weekly_exhausted",
                ),
            )
        }
    )
    scheduler, _ = await initialized_scheduler(PoolConfig(accounts=(configured,)))

    blocked: Final = await scheduler.acquire(AcquireRequest(request_id="request-a", model="model-a"))
    available: Final = await scheduler.acquire(AcquireRequest(request_id="request-b", model="model-b"))

    assert isinstance(blocked, AcquireUnavailable)
    assert blocked.reasons == ("shared:weekly_exhausted",)
    assert isinstance(available, AcquireSuccess)


@pytest.mark.asyncio
async def test_exhausted_package_balance_only_blocks_selected_model_and_recovers_after_refresh() -> None:
    package_balance: Final = QuotaWindowConfig(
        window_id="subscription-balance",
        scope=RuntimeQuotaScope.MODEL,
        subject_id="model-a",
        kind=RuntimeQuotaKind.PROVIDER_UNITS,
        window_type=RuntimeQuotaWindowType.LIFETIME,
        remaining=Decimal("0"),
        observed_at=time.time(),
        source="subscription_balance",
        reason_code="subscription_balance_exhausted",
    )
    configured: Final = account("shared", max_concurrency=2, models=("model-a", "model-b")).model_copy(
        update={"quota_windows": (package_balance,)}
    )
    scheduler, _ = await initialized_scheduler(PoolConfig(accounts=(configured,)))

    blocked: Final = await scheduler.acquire(AcquireRequest(request_id="request-a", model="model-a"))
    available: Final = await scheduler.acquire(AcquireRequest(request_id="request-b", model="model-b"))
    routes: Final = await scheduler.route_table("model-a")

    assert isinstance(blocked, AcquireUnavailable)
    assert blocked.reasons == ("shared:subscription_balance_exhausted",)
    assert routes[0].available is False
    assert routes[0].remaining_quota == Decimal("0")
    assert routes[0].remaining_quota_unit == "provider_units"
    assert isinstance(available, AcquireSuccess)

    refreshed: Final = package_balance.model_copy(update={"remaining": Decimal("5"), "observed_at": time.time()})
    await scheduler.reconfigure(PoolConfig(accounts=(configured.model_copy(update={"quota_windows": (refreshed,)}),)))
    recovered: Final = await scheduler.acquire(AcquireRequest(request_id="request-a-recovered", model="model-a"))

    assert isinstance(recovered, AcquireSuccess)


@pytest.mark.asyncio
async def test_billing_route_window_falls_back_to_sibling_route() -> None:
    configured: Final = account("shared", max_concurrency=2).model_copy(
        update={
            "quota_windows": (
                quota_window(
                    "route-window",
                    "0",
                    scope=RuntimeQuotaScope.BILLING_ROUTE,
                    subject_id="route-a",
                    reason_code="monthly_exhausted",
                ),
            ),
            "deployments": (
                DeploymentConfig(
                    public_model="model-a",
                    litellm_model_id="deployment-a",
                    billing_route_id="route-a",
                ),
                DeploymentConfig(
                    public_model="model-a",
                    litellm_model_id="deployment-b",
                    billing_route_id="route-b",
                ),
            ),
        }
    )
    scheduler, _ = await initialized_scheduler(PoolConfig(accounts=(configured,)))

    acquired: Final = await scheduler.acquire(AcquireRequest(request_id="request-a", model="model-a"))

    assert isinstance(acquired, AcquireSuccess)
    assert acquired.lease.billing_route_id == "route-b"


@pytest.mark.asyncio
async def test_token_reservation_is_released_or_replaced_by_actual_usage() -> None:
    configured: Final = account("shared", max_concurrency=2).model_copy(
        update={"quota_windows": (quota_window("token-window", "100"),)}
    )
    scheduler, store = await initialized_scheduler(PoolConfig(accounts=(configured,)))
    reserved: Final = await scheduler.acquire(
        AcquireRequest(request_id="request-a", model="model-a", estimated_tokens=80)
    )
    assert isinstance(reserved, AcquireSuccess)

    blocked: Final = await scheduler.acquire(
        AcquireRequest(request_id="request-b", model="model-a", estimated_tokens=30)
    )
    assert isinstance(blocked, AcquireUnavailable)
    assert blocked.candidates[0].stage == "reservation"
    assert blocked.candidates[0].scope == "channel"
    assert blocked.candidates[0].source == "quota"
    assert await store.release(reserved.lease.lease_id)

    replacement: Final = await scheduler.acquire(
        AcquireRequest(request_id="request-c", model="model-a", estimated_tokens=30)
    )
    assert isinstance(replacement, AcquireSuccess)
    assert await store.settle(
        SettleRequest(
            lease_id=replacement.lease.lease_id,
            success=True,
            input_tokens=10,
            output_tokens=5,
        )
    )
    assert await store.release(replacement.lease.lease_id)

    after_actual_usage: Final = await scheduler.acquire(
        AcquireRequest(request_id="request-d", model="model-a", estimated_tokens=80)
    )
    assert isinstance(after_actual_usage, AcquireSuccess)
