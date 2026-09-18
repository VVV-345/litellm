"""验证额度预筛选、套餐偏好和原生会话亲和性不扩大卡片范围。"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from litellm import Router
from litellm.proxy.management_endpoints.account_pool_integration import PoolIdentity, pool_identity
from litellm.proxy.management_endpoints.account_pool_native_routing import (
    RoutingQuota,
    RoutingSnapshot,
    eligible_deployments,
    preferred_deployments,
    snapshots,
)
from litellm.types.router import AccountPoolRoutingConfig, RouterRateLimitError, RouterRateLimitErrorBasic


def deployment(card, order=0):
    return {
        "model_name": "shared",
        "model_info": {"id": str(card), "account_pool_environment_id": str(card)},
        "litellm_params": {"model": "openai/gpt-6-astra", "api_key": "test", "order": order},
    }


def test_quota_alias_staleness_and_expiry_are_checked_without_network():
    now = datetime.now(timezone.utc)
    cards = [uuid4() for _ in range(5)]
    models = [deployment(card) for card in cards]
    values = {
        str(cards[0]): RoutingSnapshot(quota=RoutingQuota(remaining_percent=80, observed_at=now)),
        str(cards[1]): RoutingSnapshot(quota=RoutingQuota(remaining_percent=0, observed_at=now)),
        str(cards[2]): RoutingSnapshot(
            quota=RoutingQuota(remaining_percent=90, observed_at=now - timedelta(hours=1)), quota_reserve_percent=10
        ),
        str(cards[3]): RoutingSnapshot(subscription_active_until=now - timedelta(seconds=1)),
        str(cards[4]): RoutingSnapshot(
            model_aliases={"shared": "actual"}, model_quotas={"actual": RoutingQuota(remaining_percent=0)}
        ),
    }
    assert eligible_deployments(models, "shared", values, now) == [models[0]]


def test_preference_keeps_ties_and_unmanaged_deployments():
    now = datetime.now(timezone.utc)
    cards = [uuid4() for _ in range(3)]
    models = [deployment(card) for card in cards]
    direct = {"model_info": {"id": "direct"}}
    values = {
        str(card): RoutingSnapshot(plan_rank=rank, quota=RoutingQuota(remaining_percent=quota, observed_at=now))
        for card, rank, quota in zip(cards, [300, 500, 500], [90, 80, 80])
    }
    assert preferred_deployments(
        [*models, direct], "shared", AccountPoolRoutingConfig(selection="plan"), values, now
    ) == [models[1], models[2], direct]
    assert preferred_deployments(models, "shared", AccountPoolRoutingConfig(selection="quota"), values, now) == [
        models[0]
    ]
    assert preferred_deployments(models, "shared", AccountPoolRoutingConfig(), values, now) == models


@pytest.mark.asyncio
async def test_router_filters_exhausted_card_before_native_order_and_honors_scope():
    first, second = uuid4(), uuid4()
    router = Router(model_list=[deployment(first), deployment(second, 1)])
    previous = snapshots.values
    reset = pool_identity.set(PoolIdentity(key_hash="test", request_id=uuid4()))
    snapshots.replace({str(first): RoutingSnapshot(quota=RoutingQuota(remaining_percent=0))})
    try:
        selected = await router.async_get_available_deployment(model="shared", request_kwargs={})
        assert selected["model_info"]["id"] == str(second)
        pool_identity.set(PoolIdentity(key_hash="test", request_id=uuid4(), card_id=first))
        with pytest.raises((RouterRateLimitError, RouterRateLimitErrorBasic)):
            await router.async_get_available_deployment(model="shared", request_kwargs={})
    finally:
        pool_identity.reset(reset)
        snapshots.replace(previous)
        router.discard()


@pytest.mark.asyncio
async def test_native_session_pin_is_key_scoped_and_unavailable_pin_can_move():
    from litellm.router_utils.pre_call_checks.deployment_affinity_check import DeploymentAffinityCheck

    first, second = uuid4(), uuid4()
    router = Router(model_list=[deployment(first), deployment(second)], account_pool_routing={"session_affinity": True})
    previous = snapshots.values
    reset = pool_identity.set(
        PoolIdentity(key_hash="caller-a", request_id=uuid4(), headers=(("x-session-id", "same"),))
    )
    try:
        cache_key = DeploymentAffinityCheck.get_session_affinity_cache_key("shared", "same", "caller-a")
        await router.cache.async_set_cache(cache_key, {"model_id": str(second)}, ttl=3600)
        selected = await router.async_get_available_deployment(model="shared", request_kwargs={})
        assert selected["model_info"]["id"] == str(second)
        selected = await router.async_get_available_deployment(
            model="shared", request_kwargs={"_excluded_deployment_ids": [str(second)]}
        )
        assert selected["model_info"]["id"] == str(first)
        pool_identity.set(
            PoolIdentity(key_hash="caller-b", request_id=uuid4(), card_id=first, headers=(("x-session-id", "same"),))
        )
        selected = await router.async_get_available_deployment(model="shared", request_kwargs={})
        assert selected["model_info"]["id"] == str(first)
        pool_identity.set(PoolIdentity(key_hash="caller-a", request_id=uuid4(), headers=(("x-session-id", "same"),)))
        snapshots.replace({str(second): RoutingSnapshot(quota=RoutingQuota(remaining_percent=0))})
        selected = await router.async_get_available_deployment(model="shared", request_kwargs={})
        assert selected["model_info"]["id"] == str(first)
    finally:
        pool_identity.reset(reset)
        snapshots.replace(previous)
        router.discard()


@pytest.mark.asyncio
async def test_stateful_continuation_cannot_move_to_a_different_card():
    from litellm.responses.utils import ResponsesAPIRequestUtils

    first, second = uuid4(), uuid4()
    router = Router(model_list=[deployment(first), deployment(second)])
    previous = snapshots.values
    reset = pool_identity.set(PoolIdentity(key_hash="test", request_id=uuid4()))
    response_id = ResponsesAPIRequestUtils._build_responses_api_response_id("openai", str(first), "original")
    try:
        selected = await router.async_get_available_deployment(
            model="shared", request_kwargs={"previous_response_id": response_id}
        )
        assert selected["model_info"]["id"] == str(first)
        snapshots.replace({str(first): RoutingSnapshot(quota=RoutingQuota(remaining_percent=0))})
        with pytest.raises((RouterRateLimitError, RouterRateLimitErrorBasic)):
            await router.async_get_available_deployment(
                model="shared", request_kwargs={"previous_response_id": response_id}
            )
    finally:
        pool_identity.reset(reset)
        snapshots.replace(previous)
        router.discard()


def test_router_setting_updates_take_effect_and_roundtrip():
    router = Router()
    try:
        router.update_settings(
            account_pool_routing={"selection": "expiry", "session_affinity": True, "session_affinity_ttl_seconds": 900}
        )
        assert router.get_settings()["account_pool_routing"] == {
            "selection": "expiry",
            "session_affinity": True,
            "session_affinity_ttl_seconds": 900,
        }
        assert any(type(callback).__name__ == "DeploymentAffinityCheck" for callback in router.optional_callbacks)
    finally:
        router.discard()


@pytest.mark.asyncio
async def test_native_session_hook_creates_pin_with_requested_ttl():
    from litellm.constants import SESSION_DEPLOYMENT_AFFINITY_TTL_METADATA_KEY
    from litellm.router_utils.pre_call_checks.deployment_affinity_check import DeploymentAffinityCheck

    card = uuid4()
    router = Router(
        model_list=[deployment(card)],
        account_pool_routing={"session_affinity": True, "session_affinity_ttl_seconds": 90},
    )
    reset = pool_identity.set(
        PoolIdentity(key_hash="caller", request_id=uuid4(), headers=(("x-session-id", "session"),))
    )
    try:
        kwargs = {}
        selected = await router.async_get_available_deployment(model="shared", request_kwargs=kwargs)
        metadata = kwargs.get("litellm_metadata") or kwargs["metadata"]
        assert metadata[SESSION_DEPLOYMENT_AFFINITY_TTL_METADATA_KEY] == 90
        callback = next(item for item in router.optional_callbacks if isinstance(item, DeploymentAffinityCheck))
        await callback.async_pre_call_deployment_hook(
            {"metadata": {**metadata, "deployment_model_name": "shared"}, "model_info": selected["model_info"]}, None
        )
        cache_key = callback.get_session_affinity_cache_key("shared", "session", "caller")
        assert (await router.cache.async_get_cache(cache_key))["model_id"] == str(card)
    finally:
        pool_identity.reset(reset)
        router.discard()


def test_timing_records_failed_phase_without_request_contents(caplog):
    import json
    import logging

    from litellm.proxy.management_endpoints.account_pool_timing import RequestTiming

    request_id = uuid4()
    timing = RequestTiming(request_id, attempt=2)
    with pytest.raises(RuntimeError), timing.phase("resolve"):
        raise RuntimeError("private content must not enter logs")
    with caplog.at_level(logging.INFO, logger="LiteLLM Proxy"):
        timing.report(503)
    record = next(item for item in caplog.records if item.getMessage().startswith("account_pool_timing "))
    output = json.loads(record.getMessage().removeprefix("account_pool_timing "))
    assert set(output) == {"request_id", "attempt", "status", "total_ms", "phases_ms"}
    assert output["request_id"] == str(request_id)
    assert output["attempt"] == 2
    assert output["status"] == 503
    assert output["phases_ms"]["resolve"] >= 0
    assert "private content" not in record.getMessage()
