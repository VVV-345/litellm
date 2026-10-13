"""验证真实 Router 的额度耗尽切换、开关、授权边界和有限尝试。"""

from uuid import uuid4

import pytest

from litellm import ModelResponse, Router
from litellm.exceptions import RateLimitError
from litellm.proxy.management_endpoints.account_pool_integration import PoolIdentity, pool_identity
from litellm.proxy.management_endpoints.account_pool_native_routing import RoutingSnapshot, snapshots


def deployment(card, model="shared"):
    return {
        "model_name": model,
        "model_info": {"id": f"{card}-{model}", "account_pool_environment_id": str(card)},
        "litellm_params": {"model": "openai/test-model", "api_key": "test"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["least-busy", "simple-shuffle", "latency-based-routing"])
async def test_quota_exhaustion_switches_without_weighted_failover_or_retry_budget(strategy):
    cards = [uuid4(), uuid4(), uuid4()]
    router = Router(model_list=[deployment(card) for card in cards], routing_strategy=strategy, num_retries=0)
    previous = snapshots.values
    snapshots.replace({str(card): RoutingSnapshot(quota_switch_project=True) for card in cards})
    reset = pool_identity.set(PoolIdentity(key_hash="caller", request_id=uuid4()))
    attempts = []

    async def provider(**kwargs):
        chosen = await router.async_get_available_deployment(model=kwargs["model"], request_kwargs=kwargs)
        card = chosen["model_info"]["account_pool_environment_id"]
        attempts.append(card)
        if len(attempts) < 3:
            error = RateLimitError(message="insufficient_quota", model="shared", llm_provider="openai")
            error.failed_deployment_id = chosen["model_info"]["id"]
            raise error
        return ModelResponse(model="shared", choices=[{"message": {"role": "assistant", "content": "ok"}}])

    try:
        response = await router.async_function_with_fallbacks(model="shared", original_function=provider, metadata={})
        assert response.choices[0].message.content == "ok"
        assert len(attempts) == len(set(attempts)) == 3
    finally:
        pool_identity.reset(reset)
        snapshots.replace(previous)
        router.discard()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["disabled", "bound", "tools", "previous", "all_exhausted", "ordinary_429"])
async def test_quota_failover_never_escapes_scope_or_replays_unsafe_requests(mode):
    first, second = uuid4(), uuid4()
    router = Router(model_list=[deployment(first), deployment(second)], num_retries=0, max_fallbacks=5)
    previous = snapshots.values
    snapshots.replace({str(card): RoutingSnapshot(quota_switch_project=mode != "disabled") for card in (first, second)})
    reset = pool_identity.set(
        PoolIdentity(key_hash="caller", request_id=uuid4(), card_id=first if mode == "bound" else None)
    )
    attempts = []

    async def provider(**kwargs):
        chosen = await router.async_get_available_deployment(model=kwargs["model"], request_kwargs=kwargs)
        attempts.append(chosen["model_info"]["id"])
        error = RateLimitError(
            message="rate limit exceeded" if mode == "ordinary_429" else "usage_limit_reached",
            model="shared",
            llm_provider="openai",
        )
        error.failed_deployment_id = chosen["model_info"]["id"]
        raise error

    try:
        payload = (
            {"tools": [{"type": "function"}]}
            if mode == "tools"
            else {"previous_response_id": "resp_prior"}
            if mode == "previous"
            else {}
        )
        with pytest.raises(RateLimitError):
            await router.async_function_with_fallbacks(
                model="shared", original_function=provider, metadata={}, **payload
            )
        assert len(attempts) == (2 if mode == "all_exhausted" else 1)
        assert len(set(attempts)) == len(attempts)
    finally:
        pool_identity.reset(reset)
        snapshots.replace(previous)
        router.discard()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,authorized", [(True, True), (False, True), (True, False)])
async def test_quota_preview_uses_explicit_mapping_and_native_authorization(enabled, authorized):
    card = uuid4()

    async def access_check(**kwargs):
        return authorized

    router = Router(
        model_list=[deployment(card), deployment(card, "preview")],
        num_retries=0,
        fallbacks=[{"shared": ["preview"]}],
        fallback_access_check=access_check,
    )
    previous = snapshots.values
    snapshots.replace({str(card): RoutingSnapshot(quota_switch_preview_model=enabled)})
    reset = pool_identity.set(PoolIdentity(key_hash="caller", request_id=uuid4()))
    attempts = []

    async def provider(**kwargs):
        attempts.append(kwargs["model"])
        if kwargs["model"] == "shared":
            error = RateLimitError(message="quota_exceeded", model="shared", llm_provider="openai")
            error.failed_deployment_id = f"{card}-shared"
            raise error
        return ModelResponse(model="preview", choices=[{"message": {"role": "assistant", "content": "preview ok"}}])

    try:
        if enabled and authorized:
            result = await router.async_function_with_fallbacks(model="shared", original_function=provider, metadata={})
            assert result.model == "preview"
            assert attempts == ["shared", "preview"]
        else:
            with pytest.raises(RateLimitError):
                await router.async_function_with_fallbacks(model="shared", original_function=provider, metadata={})
            assert attempts == ["shared"]
    finally:
        pool_identity.reset(reset)
        snapshots.replace(previous)
        router.discard()


@pytest.mark.parametrize("code", ["usage_limit_reached", "quota_exceeded", "quota_exhausted", "insufficient_quota"])
def test_stream_quota_failure_is_classified_before_output_for_router_failover(code):
    import json
    from litellm.proxy.management_endpoints.account_pool_stream import EventStream

    state = EventStream(responses_api=True)
    payload = {"type": "response.failed", "response": {"error": {"code": code, "message": "quota exhausted"}}}
    for frame in state.feed(("data: " + json.dumps(payload) + "\n\n").encode()):
        state.observe(frame)
    assert state.failed and state.terminal and not state.meaningful
    assert state.error_status == 429


@pytest.mark.asyncio
async def test_stream_output_is_not_replayed_when_quota_failure_arrives_later():
    card = uuid4()
    router = Router(model_list=[deployment(card)], num_retries=0)
    previous = snapshots.values
    snapshots.replace({str(card): RoutingSnapshot(quota_switch_project=True, quota_switch_preview_model=True)})
    reset = pool_identity.set(PoolIdentity(key_hash="caller", request_id=uuid4()))
    attempts = []

    async def chunks():
        yield b'data: {"type":"response.output_text.delta","delta":"hello"}\n\n'
        raise RateLimitError(message="usage_limit_reached", model="shared", llm_provider="openai")

    async def provider(**kwargs):
        attempts.append(kwargs["model"])
        return chunks()

    try:
        stream = await router.async_function_with_fallbacks(model="shared", original_function=provider, metadata={})
        assert b"hello" in await anext(stream)
        with pytest.raises(RateLimitError):
            await anext(stream)
        assert attempts == ["shared"]
    finally:
        pool_identity.reset(reset)
        snapshots.replace(previous)
        router.discard()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_known_depleted_prefilter_obeys_preview_switch(enabled):
    from litellm.proxy.management_endpoints.account_pool_native_routing import RoutingQuota
    from litellm.types.router import RouterRateLimitError, RouterRateLimitErrorBasic

    card = uuid4()
    router = Router(
        model_list=[deployment(card), deployment(card, "preview")], num_retries=0, fallbacks=[{"shared": ["preview"]}]
    )
    previous = snapshots.values
    snapshots.replace(
        {
            str(card): RoutingSnapshot(
                quota_switch_preview_model=enabled,
                model_quotas={"shared": RoutingQuota(remaining_percent=0)},
            )
        }
    )
    reset = pool_identity.set(PoolIdentity(key_hash="caller", request_id=uuid4(), card_id=card))
    attempts = []

    async def provider(**kwargs):
        await router.async_get_available_deployment(model=kwargs["model"], request_kwargs=kwargs)
        attempts.append(kwargs["model"])
        return ModelResponse(model=kwargs["model"])

    try:
        if enabled:
            response = await router.async_function_with_fallbacks(
                model="shared", original_function=provider, metadata={}
            )
            assert response.model == "preview"
            assert attempts == ["preview"]
        else:
            with pytest.raises((RouterRateLimitError, RouterRateLimitErrorBasic, RateLimitError)):
                await router.async_function_with_fallbacks(model="shared", original_function=provider, metadata={})
            assert attempts == []
    finally:
        pool_identity.reset(reset)
        snapshots.replace(previous)
        router.discard()


@pytest.mark.asyncio
async def test_quota_during_retry_stops_same_card_retries(monkeypatch):
    from litellm.exceptions import InternalServerError

    first, second = uuid4(), uuid4()
    ordered = [deployment(first), deployment(second)]
    ordered[0]["litellm_params"]["order"] = 0
    ordered[1]["litellm_params"]["order"] = 1
    router = Router(model_list=ordered, num_retries=3, retry_after=0, disable_cooldowns=True)
    monkeypatch.setattr(router, "_time_to_sleep_before_retry", lambda **kwargs: 0)
    previous = snapshots.values
    snapshots.replace({str(card): RoutingSnapshot(quota_switch_project=True) for card in (first, second)})
    reset = pool_identity.set(PoolIdentity(key_hash="caller", request_id=uuid4()))
    attempts = []

    async def provider(**kwargs):
        chosen = await router.async_get_available_deployment(model=kwargs["model"], request_kwargs=kwargs)
        card = chosen["model_info"]["account_pool_environment_id"]
        attempts.append(card)
        if card == str(first):
            error = (
                InternalServerError(message="busy", model="shared", llm_provider="openai")
                if len(attempts) == 1
                else RateLimitError(message="insufficient_quota", model="shared", llm_provider="openai")
            )
            error.failed_deployment_id = chosen["model_info"]["id"]
            raise error
        return ModelResponse(model="shared")

    try:
        await router.async_function_with_fallbacks(model="shared", original_function=provider, metadata={})
        assert attempts == [str(first), str(first), str(second)]
    finally:
        pool_identity.reset(reset)
        snapshots.replace(previous)
        router.discard()
