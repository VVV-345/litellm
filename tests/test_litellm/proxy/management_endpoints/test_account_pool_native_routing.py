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


def test_explicit_card_order_only_narrows_existing_candidates_and_preserves_direct_routes():
    now = datetime.now(timezone.utc)
    first, second, outside = (str(uuid4()) for _ in range(3))
    models = [deployment(first), deployment(second)]
    direct = {"model_info": {"id": "direct"}}
    config = AccountPoolRoutingConfig(selection="ordered", preferred_account_ids=(outside, second, first))
    assert preferred_deployments([*models, direct], "shared", config, {}, now) == [models[1], direct]
    assert preferred_deployments([models[0]], "shared", config, {}, now) == [models[0]]
    assert preferred_deployments([], "shared", config, {}, now) == []


def test_elapsed_quota_window_allows_probe_without_claiming_fresh_quota():
    from litellm.proxy.management_endpoints.account_pool_native_routing import available
    from litellm.proxy.management_endpoints.account_pool_reconciler import QuotaSnapshot

    now = datetime.now(timezone.utc)
    quota = QuotaSnapshot.model_validate({"observed_at": now - timedelta(hours=1), "windows": [
        {"remaining_percent": 0, "resets_at": now - timedelta(seconds=1)},
        {"remaining_percent": 65, "resets_at": now + timedelta(days=1)},
    ]}).routing_quota()
    assert available(RoutingSnapshot(model_quotas={"shared": quota}), "shared", now)
    assert not available(RoutingSnapshot(model_quotas={"shared": quota}, quota_reserve_percent=10), "shared", now)
    assert not available(RoutingSnapshot(model_quotas={"shared": quota}), "shared", now - timedelta(seconds=2))


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
    from litellm.proxy._types import ConfigYAML

    router = Router()
    try:
        request = ConfigYAML.model_validate(
            {
                "router_settings": {
                    "account_pool_routing": {
                        "selection": "expiry",
                        "session_affinity": True,
                        "session_affinity_ttl_seconds": 900,
                    },
                    "enable_weighted_failover": True,
                }
            }
        )
        persisted = request.router_settings.model_dump(exclude_none=True)
        router.update_settings(**persisted)
        assert router.get_settings()["enable_weighted_failover"] is True
        assert router.get_settings()["account_pool_routing"] == {
            "preferred_account_ids": (),
            "selection": "expiry",
            "session_affinity": True,
            "session_affinity_ttl_seconds": 900,
        }
        assert any(type(callback).__name__ == "DeploymentAffinityCheck" for callback in router.optional_callbacks)
        reloaded = Router(**persisted)
        try:
            assert reloaded.get_settings()["account_pool_routing"] == router.get_settings()["account_pool_routing"]
            assert reloaded.get_settings()["enable_weighted_failover"] is True
        finally:
            reloaded.discard()
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


@pytest.mark.asyncio
async def test_body_session_pins_signed_history_and_refuses_unavailable_card():
    from litellm.exceptions import ServiceUnavailableError
    from litellm.router_utils.pre_call_checks.deployment_affinity_check import DeploymentAffinityCheck

    first, second = uuid4(), uuid4()
    router = Router(model_list=[deployment(first), deployment(second)])
    previous = snapshots.values
    reset = pool_identity.set(PoolIdentity(key_hash="caller", request_id=uuid4()))
    body = {"metadata": {"user_id": '{"session_id":"body-session"}'}, "messages": [
        {"role": "assistant", "content": [{"type": "thinking", "signature": "old"}]},
        {"role": "user", "content": "continue"},
    ]}
    try:
        cache_key = DeploymentAffinityCheck.get_session_affinity_cache_key("shared", "body-session", "caller")
        await router.cache.async_set_cache(cache_key, {"model_id": str(second)}, ttl=3600)
        selected = await router.async_get_available_deployment(model="shared", request_kwargs=body)
        assert selected["model_info"]["id"] == str(second)
        snapshots.replace({str(second): RoutingSnapshot(quota=RoutingQuota(remaining_percent=0))})
        with pytest.raises(ServiceUnavailableError, match="signed state"):
            await router.async_get_available_deployment(model="shared", request_kwargs=body)
    finally:
        pool_identity.reset(reset)
        snapshots.replace(previous)
        router.discard()


@pytest.mark.asyncio
async def test_signed_pin_error_cannot_be_bypassed_by_outer_retry_or_fallback():
    from litellm.proxy.management_endpoints.account_pool_session import AccountPoolSessionUnavailableError
    calls = []
    async def unavailable(**kwargs):
        calls.append(kwargs["model"])
        raise AccountPoolSessionUnavailableError(message="signed state unavailable", model="shared", llm_provider="account_pool")
    router = Router(model_list=[deployment(uuid4())], num_retries=3, fallbacks=[{"shared": ["backup"]}])
    try:
        with pytest.raises(AccountPoolSessionUnavailableError):
            await router.async_function_with_fallbacks(model="shared", original_function=unavailable)
        assert calls == ["shared"]
    finally:
        router.discard()


@pytest.mark.asyncio
async def test_signed_pool_request_cannot_fallback_before_gateway_acquisition():
    from litellm.exceptions import ServiceUnavailableError
    calls = []
    async def unavailable(**kwargs):
        calls.append(kwargs["model"])
        raise ServiceUnavailableError(message="no cards", model="shared", llm_provider="account_pool")
    router = Router(model_list=[deployment(uuid4())], num_retries=0, fallbacks=[{"shared": ["backup"]}])
    reset = pool_identity.set(PoolIdentity(key_hash="caller", request_id=uuid4()))
    try:
        with pytest.raises(ServiceUnavailableError):
            await router.async_function_with_fallbacks(model="shared", original_function=unavailable,
                messages=[{"role": "assistant", "content": [{"type": "thinking", "signature": "old"}]}])
        assert calls == ["shared"]
    finally:
        pool_identity.reset(reset)
        router.discard()


@pytest.mark.asyncio
async def test_native_messages_stream_reuses_gateway_retry_boundary(monkeypatch):
    monkeypatch.setenv("ACCOUNT_POOL_MANAGER_TOKEN", "test-only-manager-token-" * 3)
    router = Router(model_list=[deployment(uuid4())])
    reset = pool_identity.set(PoolIdentity(key_hash="caller", request_id=uuid4()))
    async def chunks():
        yield b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error","message":"busy"}}\n\n'
    stream = chunks()
    async def provider(**kwargs):
        return stream
    try:
        response = await router._aanthropic_messages_with_streaming_fallbacks(
            original_function=provider, model="shared", stream=True, litellm_metadata={},
            messages=[{"role": "user", "content": "go"}])
        assert await anext(response) == b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error","message":"busy"}}\n\n'
        with pytest.raises(StopAsyncIteration):
            await anext(response)
    finally:
        await stream.aclose()
        pool_identity.reset(reset)
        router.discard()


@pytest.mark.asyncio
async def test_model_switch_uses_native_encryption_boundary_without_expanding_key_scope():
    from litellm.exceptions import BadRequestError, ServiceUnavailableError
    from litellm.responses.utils import ResponsesAPIRequestUtils

    first, second = uuid4(), uuid4()
    origin = deployment(first)
    origin["litellm_params"]["api_base"] = "http://card-a/v1"
    same_card = {
        **deployment(first),
        "model_name": "other",
        "model_info": {"id": "other-a", "account_pool_environment_id": str(first)},
        "litellm_params": {"model": "openai/gpt-5.6-terra", "api_key": "test", "api_base": "http://card-a/v1"},
    }
    other_card = {
        **deployment(second),
        "model_name": "other",
        "litellm_params": {"model": "openai/gpt-5.6-terra", "api_key": "test", "api_base": "http://card-b/v1"},
    }
    router = Router(
        model_list=[origin, same_card, other_card],
        enable_pre_call_checks=True,
        optional_pre_call_checks=["encrypted_content_affinity"],
    )
    reset = pool_identity.set(PoolIdentity(key_hash="caller", request_id=uuid4()))
    encoded = ResponsesAPIRequestUtils._build_encrypted_item_id(str(first), "rs_old")
    try:
        selected = await router.async_get_available_deployment(
            model="other",
            request_kwargs={"input": [{"type": "reasoning", "id": encoded, "encrypted_content": "opaque"}]},
        )
        assert selected["model_info"]["id"] == "other-a"
        pool_identity.set(PoolIdentity(key_hash="caller", request_id=uuid4(), card_id=second))
        with pytest.raises((BadRequestError, RouterRateLimitError, RouterRateLimitErrorBasic, ServiceUnavailableError)):
            await router.async_get_available_deployment(
                model="other",
                request_kwargs={"input": [{"type": "reasoning", "id": encoded, "encrypted_content": "opaque"}]},
            )
    finally:
        pool_identity.reset(reset)
        router.discard()


def test_foreign_signed_history_is_recovered_before_native_affinity_without_bypassing_scope():
    from litellm.proxy.management_endpoints.account_pool_signature import foreign_history_recovery
    from litellm.responses.utils import ResponsesAPIRequestUtils

    card = uuid4()
    router = Router(model_list=[deployment(card)])
    model = deployment(card)["model_name"]
    reset = pool_identity.set(PoolIdentity(key_hash="caller", request_id=uuid4(), card_id=card))
    history = [
        {"role": "user", "content": "compute"},
        {
            "type": "reasoning",
            "id": ResponsesAPIRequestUtils._build_encrypted_item_id("foreign", "rs_old"),
            "encrypted_content": "opaque",
        },
        {"type": "function_call", "call_id": "c1", "name": "sum", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "2"},
    ]
    try:
        payload = {"model": model, "input": history}
        recovered = foreign_history_recovery(payload, router)
        assert recovered["input"] == [history[0], history[2], history[3]]
        assert len(payload["input"]) == 4
        assert foreign_history_recovery({**payload, "previous_response_id": "resp_old"}, router) == {}
        assert foreign_history_recovery({**payload, "input": history[:-1]}, router) == {}
        local = {**history[1], "id": ResponsesAPIRequestUtils._build_encrypted_item_id(str(card), "rs_old")}
        assert foreign_history_recovery({**payload, "input": [history[0], local, *history[2:]]}, router) == {}
        assert pool_identity.get().card_id == card
    finally:
        pool_identity.reset(reset)
        router.discard()
