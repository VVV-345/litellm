"""验证真实 Router 的额度耗尽切换、开关、授权边界和有限尝试。"""

from uuid import uuid4

import pytest

from litellm import ModelResponse, Router
from litellm.exceptions import RateLimitError
from litellm.proxy.management_endpoints.account_pool_integration import PoolIdentity, pool_identity
from litellm.proxy.management_endpoints.account_pool_native_routing import RoutingSnapshot, snapshots


@pytest.fixture
def native_responses_pool(monkeypatch):
    monkeypatch.setenv("ACCOUNT_POOL_MANAGER_TOKEN", "test-native-conversation-" * 2)
    monkeypatch.setenv("ACCOUNT_POOL_DEGRADED_CONTINUATION", "1")
    cards = (uuid4(), uuid4(), uuid4())
    router = Router(
        model_list=[
            {**deployment(card), "litellm_params": {**deployment(card)["litellm_params"], "order": index}}
            for index, card in enumerate(cards)
        ],
        num_retries=0,
        max_fallbacks=3,
    )
    before = snapshots.values
    snapshots.replace({str(card): RoutingSnapshot(quota_switch_project=True) for card in cards})
    token = pool_identity.set(PoolIdentity(key_hash="caller", request_id=uuid4()))
    try:
        yield router, tuple(f"{card}-shared" for card in cards)
    finally:
        pool_identity.reset(token)
        snapshots.replace(before)
        router.discard()


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


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["insufficient_quota", "usage_limit_reached", "quota_exceeded", "quota_exhausted"])
async def test_native_responses_stream_quota_switches_before_output(native_responses_pool, code):
    from litellm.responses.streaming_iterator import BaseResponsesAPIStreamingIterator
    from litellm.types.llms.openai import ErrorEvent, ErrorEventError, OutputTextDeltaEvent

    router, cards = native_responses_pool
    attempts = []

    class NativeStream(BaseResponsesAPIStreamingIterator):
        def __init__(self, card):
            self.card = card
            self._hidden_params = {"model_id": card}
            self.custom_llm_provider = "openai"
            self.model = "shared"
            self._generated_content = ""
            self._yielded_first_chunk = False
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.card != cards[-1]:
                self._maybe_raise_for_error_event(ErrorEvent(
                    type="error", sequence_number=0,
                    error=ErrorEventError(type="rate_limit_error", code=code, message="Quota exhausted"),
                ))
            return OutputTextDeltaEvent(
                type="response.output_text.delta", delta="continued", item_id="m", output_index=0, content_index=0,
            )

        async def aclose(self):
            self.closed = True

    sources = []

    async def provider(**kwargs):
        card = kwargs["litellm_metadata"]["model_info"]["id"]
        attempts.append(card)
        assert kwargs["input"] in ("Keep history", [{"role": "user", "content": "Keep history"}])
        source = NativeStream(card)
        sources.append(source)
        return source

    stream = await router._aresponses_with_streaming_fallbacks(
        original_function=provider, model="shared", input="Keep history", stream=True,
    )
    assert (await anext(stream)).delta == "continued"
    assert attempts == list(cards)
    assert all(source.closed for source in sources[:-1])
    await stream.aclose()


@pytest.mark.asyncio
async def test_native_responses_created_status_is_buffered_before_quota_recovery(native_responses_pool):
    from litellm.responses.streaming_iterator import BaseResponsesAPIStreamingIterator
    from litellm.types.llms.openai import (
        ErrorEvent,
        ErrorEventError,
        OutputTextDeltaEvent,
        ResponseCreatedEvent,
        ResponsesAPIResponse,
    )

    router, cards = native_responses_pool
    attempts = []

    class NativeStream(BaseResponsesAPIStreamingIterator):
        def __init__(self, card):
            self.card = card
            self._hidden_params = {"model_id": card}
            self.custom_llm_provider = "openai"
            self.model = "shared"
            self._generated_content = ""
            self._yielded_first_chunk = False
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.card != cards[-1] and not self._yielded_first_chunk:
                self._yielded_first_chunk = True
                return ResponseCreatedEvent(
                    type="response.created",
                    response=ResponsesAPIResponse(id="resp_pending", created_at=1, model="shared", status="in_progress", output=[]),
                )
            if self.card != cards[-1]:
                self._maybe_raise_for_error_event(ErrorEvent(
                    type="error", sequence_number=0,
                    error=ErrorEventError(type="rate_limit_error", code="insufficient_quota", message="Quota exhausted"),
                ))
            return OutputTextDeltaEvent(
                type="response.output_text.delta", delta="continued", item_id="m", output_index=0, content_index=0,
            )

        async def aclose(self):
            self.closed = True

    sources = []

    async def provider(**kwargs):
        card = kwargs["litellm_metadata"]["model_info"]["id"]
        attempts.append(card)
        source = NativeStream(card)
        sources.append(source)
        return source

    stream = await router._aresponses_with_streaming_fallbacks(
        original_function=provider, model="shared", input="Keep history", stream=True,
    )
    assert (await anext(stream)).delta == "continued"
    assert attempts == list(cards)
    assert all(source.closed for source in sources[:-1])
    await stream.aclose()


@pytest.mark.asyncio
async def test_normal_first_event_wait_is_not_limited_by_retry_budget(native_responses_pool):
    import asyncio
    import time

    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation, ConversationStream
    from litellm.types.llms.openai import OutputTextDeltaEvent

    router, _ = native_responses_pool
    identity = pool_identity.get()
    token = pool_identity.set(identity.model_copy(update={"retry_deadline": time.time() + 0.02}))
    try:
        conversation = await Conversation.start(router, {"model": "shared", "input": "Hello", "timeout": 1})

        class Source:
            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.sleep(0.06)
                return OutputTextDeltaEvent(
                    type="response.output_text.delta", delta="within request timeout", item_id="m",
                    output_index=0, content_index=0,
                )

        stream = ConversationStream(Source(), conversation)
        await stream.prepare()
        assert (await anext(stream)).delta == "within request timeout"
    finally:
        pool_identity.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("code,started", [("rate_limit_exceeded", False), ("insufficient_quota", True)])
async def test_native_wrapped_error_does_not_replay_ordinary_rate_limit_or_committed_output(
    native_responses_pool, code, started,
):
    from litellm.exceptions import MidStreamFallbackError
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation
    from litellm.responses.streaming_iterator import BaseResponsesAPIStreamingIterator
    from litellm.types.llms.openai import ErrorEvent, ErrorEventError

    router, cards = native_responses_pool
    conversation = await Conversation.start(router, {"model": "shared", "input": "Keep history"})

    class Source(BaseResponsesAPIStreamingIterator):
        def __init__(self):
            self.custom_llm_provider = "openai"
            self.model = "shared"
            self._generated_content = "already delivered" if started else ""
            self._yielded_first_chunk = started

    with pytest.raises(MidStreamFallbackError) as error:
        Source()._maybe_raise_for_error_event(ErrorEvent(
            type="error", sequence_number=0,
            error=ErrorEventError(type="rate_limit_error", code=code, message="Try later"),
        ))
    error.value.failed_deployment_id = cards[0]
    assert conversation.rebuild(router, {"model": "shared", "input": "Keep history"}, error.value) is None
