import copy
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_missing_history_has_actionable_error_only_when_migration_is_needed(pool):
    import httpx

    from litellm.exceptions import BadRequestError, ServiceUnavailableError
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation, ConversationUnavailableError

    router, (first, second) = pool
    payload = {"model": "shared", "input": "Continue", "previous_response_id": response(first).id}
    conversation = await Conversation.start(router, payload)
    assert conversation is not None
    calls = []
    async def invoke(value):
        calls.append(value)
    error = ServiceUnavailableError(
        message="quota exhausted", model="shared", llm_provider="openai",
        response=httpx.Response(503, headers={"x-account-pool-failure": "quota_exhausted"},
                                request=httpx.Request("POST", "http://local/v1/responses")),
    )
    error.failed_deployment_id = str(first)
    with pytest.raises(ConversationUnavailableError) as failure:
        await conversation.recover(router, payload, error, invoke)
    assert failure.value.openai_code == "account_pool_conversation_unavailable"
    assert "完整对话历史" in str(failure.value)
    from unittest.mock import AsyncMock

    from starlette.requests import Request

    from litellm.proxy._types import ProxyException, UserAPIKeyAuth
    from litellm.proxy.common_request_processing import ProxyBaseLLMRequestProcessing
    from litellm.proxy.proxy_server import openai_exception_handler
    logging = AsyncMock()
    logging.post_call_failure_hook.return_value = None
    logging.post_call_response_headers_hook.return_value = {}
    with pytest.raises(ProxyException) as public:
        await ProxyBaseLLMRequestProcessing(data={})._handle_llm_api_exception(
            failure.value, UserAPIKeyAuth(), logging,
        )
    request = Request({"type": "http", "method": "POST", "path": "/v1/responses", "headers": []})
    result = await openai_exception_handler(request, public.value)
    import json
    assert result.status_code == 503
    assert json.loads(result.body)["error"]["code"] == "account_pool_conversation_unavailable"
    assert calls == []
    invalid = BadRequestError(message="invalid parameter", model="shared", llm_provider="openai")
    with pytest.raises(BadRequestError) as unchanged:
        await conversation.recover(router, payload, invalid, invoke)
    assert unchanged.value is invalid

from litellm import Router
from litellm.exceptions import RateLimitError
from litellm.proxy.management_endpoints.account_pool_conversation import ConversationUnavailableError
from litellm.proxy.management_endpoints.account_pool_integration import PoolIdentity, pool_identity
from litellm.proxy.management_endpoints.account_pool_native_routing import RoutingQuota, RoutingSnapshot, snapshots
from litellm.responses.utils import ResponsesAPIRequestUtils
from litellm.types.llms.openai import ResponsesAPIResponse


@pytest.fixture
def pool(monkeypatch):
    monkeypatch.setenv("ACCOUNT_POOL_MANAGER_TOKEN", "test-conversation-secret-" * 2)
    monkeypatch.setenv("ACCOUNT_POOL_DEGRADED_CONTINUATION", "0")
    cards = (uuid4(), uuid4())
    router = Router(
        model_list=[
            {
                "model_name": "shared",
                "model_info": {"id": str(card), "account_pool_environment_id": str(card)},
                "litellm_params": {"model": "openai/test-model", "api_key": "test", "order": index},
            }
            for index, card in enumerate(cards)
        ],
        num_retries=0,
        max_fallbacks=2,
        optional_pre_call_checks=["encrypted_content_affinity", "responses_api_deployment_check"],
    )
    before = snapshots.values
    snapshots.replace({str(card): RoutingSnapshot(quota_switch_project=True) for card in cards})
    token = pool_identity.set(PoolIdentity(key_hash="caller", request_id=uuid4()))
    yield router, cards
    pool_identity.reset(token)
    snapshots.replace(before)
    router.discard()


def response(card, text="What is the intended use?", output=None):
    result = ResponsesAPIResponse(
        id=ResponsesAPIRequestUtils._build_responses_api_response_id("openai", str(card), "resp_" + uuid4().hex),
        created_at=1,
        model="shared",
        status="completed",
        output=output or [{"type": "message", "role": "assistant", "content": text}],
    )
    result._hidden_params = {"model_id": str(card)}
    return result


def chosen(kwargs):
    return kwargs["litellm_metadata"]["model_info"]["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("updated_snapshot", [False, True])
async def test_gateway_quota_503_rebuilds_saved_history_despite_failed_deployment(pool, updated_snapshot):
    import httpx

    from litellm.exceptions import ServiceUnavailableError

    router, (first, second) = pool
    calls = []

    async def provider(**kwargs):
        card = chosen(kwargs)
        calls.append((card, copy.deepcopy(kwargs.get("input")), kwargs.get("previous_response_id")))
        if len(calls) > 1 and card == str(first):
            if updated_snapshot:
                snapshots.replace(
                    {
                        str(first): RoutingSnapshot(quota_switch_project=True, quota=RoutingQuota(remaining_percent=0)),
                        str(second): RoutingSnapshot(quota_switch_project=True),
                    }
                )
            raise ServiceUnavailableError(
                message='{"error":{"type":"account_pool_error","code":"account_pool_quota_exhausted"}}',
                model="shared",
                llm_provider="openai",
                response=httpx.Response(
                    503,
                    headers={"x-account-pool-failure": "quota_exhausted"},
                    request=httpx.Request("POST", "http://local/v1/responses"),
                ),
            )
        return response(card)

    initial = await router._aresponses_with_streaming_fallbacks(
        original_function=provider, model="shared", input="Budget 5000"
    )
    result = await router._aresponses_with_streaming_fallbacks(
        original_function=provider, model="shared", input="Continue", previous_response_id=initial.id
    )
    assert result.status == "completed"
    assert [call[0] for call in calls] == [str(first), str(first), str(second)]
    assert calls[-1][2] is None
    assert calls[-1][1] == [
        {"role": "user", "content": "Budget 5000"},
        {"type": "message", "role": "assistant", "content": "What is the intended use?"},
        {"role": "user", "content": "Continue"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("preemptive", [False, True])
async def test_saved_history_rebuilds_on_new_card_and_next_turn_uses_new_response(pool, preemptive):
    router, (first, second) = pool
    calls = []

    async def provider(**kwargs):
        calls.append(copy.deepcopy({key: kwargs.get(key) for key in ("input", "previous_response_id")}))
        card = chosen(kwargs)
        if len(calls) > 1 and card == str(first):
            raise RateLimitError(message="insufficient_quota", model="shared", llm_provider="openai")
        return response(card, "Budget 5000, for editing" if card == str(second) else "What is the intended use?")

    initial = await router._aresponses_with_streaming_fallbacks(
        original_function=provider, model="shared", input="My budget is 5000"
    )
    if preemptive:
        snapshots.replace(
            {
                str(first): RoutingSnapshot(quota_switch_project=True, quota=RoutingQuota(remaining_percent=0)),
                str(second): RoutingSnapshot(quota_switch_project=True),
            }
        )
    continued = await router._aresponses_with_streaming_fallbacks(
        original_function=provider, model="shared", input="For video editing", previous_response_id=initial.id
    )
    assert ResponsesAPIRequestUtils.get_model_id_from_response_id(continued.id) == str(second)
    assert calls[-1] == {
        "previous_response_id": None,
        "input": [
            {"role": "user", "content": "My budget is 5000"},
            {"type": "message", "role": "assistant", "content": "What is the intended use?"},
            {"role": "user", "content": "For video editing"},
        ],
    }
    assert len(calls) == (2 if preemptive else 3)
    await router._aresponses_with_streaming_fallbacks(
        original_function=provider, model="shared", input="Which GPU?", previous_response_id=continued.id
    )
    assert calls[-1]["previous_response_id"] == continued.id


@pytest.mark.asyncio
async def test_full_signed_history_migrates_without_saving_store_false(pool):
    router, (first, second) = pool
    attempts = []
    original = [
        {"role": "user", "content": "Budget 5000"},
        {"type": "reasoning", "encrypted_content": "opaque-old-state"},
        {"role": "assistant", "content": "For what use?"},
        {"role": "user", "content": "Editing"},
    ]
    unchanged = copy.deepcopy(original)

    async def provider(**kwargs):
        attempts.append(chosen(kwargs))
        if chosen(kwargs) == str(first):
            raise RateLimitError(message="quota_exhausted", model="shared", llm_provider="openai")
        assert kwargs["input"] == [unchanged[0], unchanged[2], unchanged[3]]
        return response(second)

    result = await router._aresponses_with_streaming_fallbacks(
        original_function=provider, model="shared", input=original, store=False
    )
    assert result.status == "completed"
    assert attempts == [str(first), str(second)]
    assert original == unchanged


@pytest.mark.asyncio
async def test_signed_marker_at_reserve_threshold_rebuilds_before_upstream(pool):
    router, (first, second) = pool
    snapshots.replace(
        {
            str(first): RoutingSnapshot(
                quota_switch_project=True, quota_reserve_percent=5, quota=RoutingQuota(remaining_percent=4)
            ),
            str(second): RoutingSnapshot(quota_switch_project=True),
        }
    )
    encrypted = ResponsesAPIRequestUtils._wrap_encrypted_content_with_model_id("old-state", str(first))
    attempts = []

    async def provider(**kwargs):
        attempts.append(chosen(kwargs))
        assert kwargs["input"] == [{"role": "user", "content": "Budget 5000"}, {"role": "user", "content": "Editing"}]
        return response(second)

    result = await router._aresponses_with_streaming_fallbacks(
        original_function=provider,
        model="shared",
        store=False,
        input=[
            {"role": "user", "content": "Budget 5000"},
            {"type": "reasoning", "encrypted_content": encrypted},
            {"role": "user", "content": "Editing"},
        ],
    )
    assert result.status == "completed"
    assert attempts == [str(second)]


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["missing", "other_key", "store_false", "disabled", "card_bound"])
async def test_missing_or_unauthorized_history_does_not_cross_cards(pool, blocked):
    router, (first, second) = pool
    attempts = []

    async def provider(**kwargs):
        attempts.append(chosen(kwargs))
        if len(attempts) > 1:
            raise RateLimitError(message="insufficient_quota", model="shared", llm_provider="openai")
        return response(first)

    initial = await router._aresponses_with_streaming_fallbacks(
        original_function=provider, model="shared", input="Private history", store=blocked != "store_false"
    )
    token = pool_identity.set(
        PoolIdentity(
            key_hash="someone-else" if blocked == "other_key" else "caller",
            request_id=uuid4(),
            card_id=first if blocked == "card_bound" else None,
        )
    )
    if blocked == "disabled":
        snapshots.replace({str(card): RoutingSnapshot() for card in (first, second)})
    try:
        with pytest.raises(Exception):
            await router._aresponses_with_streaming_fallbacks(
                original_function=provider,
                model="shared",
                input="Next",
                previous_response_id="resp_missing" if blocked == "missing" else initial.id,
            )
        assert str(second) not in attempts
    finally:
        pool_identity.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe",
    [
        {"background": True},
        {"disable_fallbacks": True},
        {"max_fallbacks": 0},
        {"extra_body": {"previous_response_id": "resp_unknown"}},
        {"conversation": "conv_server"},
        {"truncation": "auto"},
        {"input": [{"role": "user", "content": [{"type": "input_file", "file_id": "file_A"}]}]},
        {"input": [{"role": "user", "content": "hello"}, {"type": "compaction", "encrypted_content": "state"}]},
        {
            "input": [
                {"role": "user", "content": "hello"},
                {"type": "function_call_output", "call_id": "missing", "output": "ok"},
            ]
        },
    ],
)
async def test_unsafe_context_never_replays(pool, unsafe):
    router, (first, second) = pool
    attempts = []

    async def provider(**kwargs):
        attempts.append(chosen(kwargs))
        raise RateLimitError(message="quota_exhausted", model="shared", llm_provider="openai")

    with pytest.raises(Exception):
        await router._aresponses_with_streaming_fallbacks(
            original_function=provider,
            model="shared",
            **{
                "input": [{"role": "user", "content": "hello"}, {"type": "reasoning", "encrypted_content": "state"}],
                **unsafe,
            },
        )
    assert attempts == [str(first)]


@pytest.mark.asyncio
async def test_invalid_prompt_is_not_reclassified_by_depleted_snapshot(pool):
    from litellm.exceptions import BadRequestError

    router, (first, second) = pool
    attempts = []

    async def provider(**kwargs):
        attempts.append(chosen(kwargs))
        snapshots.replace(
            {
                str(first): RoutingSnapshot(quota_switch_project=True, quota=RoutingQuota(remaining_percent=0)),
                str(second): RoutingSnapshot(quota_switch_project=True),
            }
        )
        raise BadRequestError(message="invalid_prompt", model="shared", llm_provider="openai")

    with pytest.raises(BadRequestError):
        await router._aresponses_with_streaming_fallbacks(
            original_function=provider,
            model="shared",
            input=[{"role": "user", "content": "hello"}, {"type": "reasoning", "encrypted_content": "old"}],
        )
    assert attempts == [str(first)]


@pytest.mark.asyncio
async def test_stream_completion_is_saved_before_delivery_and_can_migrate(pool):
    from litellm.responses.streaming_iterator import BaseResponsesAPIStreamingIterator
    from litellm.types.llms.openai import ResponseCompletedEvent

    router, (first, second) = pool
    completed = response(first)

    class Stream(BaseResponsesAPIStreamingIterator):
        def __init__(self):
            self.events = iter([ResponseCompletedEvent(type="response.completed", response=completed)])
            self._hidden_params = {"model_id": str(first)}

        def __aiter__(self):
            return self

        async def __anext__(self):
            item = next(self.events, None)
            if item is None:
                raise StopAsyncIteration
            return item

    async def provider(**kwargs):
        if kwargs.get("stream"):
            return Stream()
        assert chosen(kwargs) == str(second)
        assert kwargs["input"][0] == {"role": "user", "content": "Budget 5000"}
        return response(second)

    stream = await router._aresponses_with_streaming_fallbacks(
        original_function=provider, model="shared", input="Budget 5000", stream=True
    )
    assert isinstance(stream, BaseResponsesAPIStreamingIterator)
    assert (await anext(stream)).response.id == completed.id
    snapshots.replace(
        {
            str(first): RoutingSnapshot(quota_switch_project=True, quota=RoutingQuota(remaining_percent=0)),
            str(second): RoutingSnapshot(quota_switch_project=True),
        }
    )
    result = await router._aresponses_with_streaming_fallbacks(
        original_function=provider, model="shared", input="Editing", previous_response_id=completed.id
    )
    assert ResponsesAPIRequestUtils.get_model_id_from_response_id(result.id) == str(second)


@pytest.mark.asyncio
async def test_tool_result_replay_does_not_repeat_the_tool_call(pool):
    router, (first, second) = pool
    initial_output = [{"type": "function_call", "call_id": "call_one", "name": "lookup", "arguments": "{}"}]
    calls = []

    async def provider(**kwargs):
        calls.append(chosen(kwargs))
        if len(calls) == 1:
            return response(first, output=initial_output)
        if chosen(kwargs) == str(first):
            raise RateLimitError(message="quota_exhausted", model="shared", llm_provider="openai")
        assert kwargs["input"] == [
            {"role": "user", "content": "Lookup budget"},
            initial_output[0],
            {"type": "function_call_output", "call_id": "call_one", "output": "5000"},
        ]
        return response(second, "Budget is 5000")

    initial = await router._aresponses_with_streaming_fallbacks(
        original_function=provider,
        model="shared",
        input="Lookup budget",
        tools=[{"type": "function", "name": "lookup", "parameters": {"type": "object"}}],
    )
    result = await router._aresponses_with_streaming_fallbacks(
        original_function=provider,
        model="shared",
        previous_response_id=initial.id,
        input=[{"type": "function_call_output", "call_id": "call_one", "output": "5000"}],
    )
    assert result.status == "completed"
    assert calls == [str(first), str(first), str(second)]


@pytest.mark.asyncio
async def test_cache_is_encrypted_scoped_bounded_and_expiring():
    import time

    from litellm.caching.dual_cache import DualCache
    from litellm.proxy.management_endpoints.account_pool_conversation import ConversationRecord, ConversationStore

    cache = DualCache()
    store = ConversationStore(cache, "secret-for-unit-test" * 3)
    identity = PoolIdentity(key_hash="key-a", request_id=uuid4())
    record = ConversationRecord(
        input=({"role": "user", "content": "private budget"},), deployment_id="card-a", expires_at=time.time() + 300
    )
    await store.save(identity, "model-a", "response-a", record)
    raw = await cache.async_get_cache(store.key(identity, "model-a", "response-a"))
    assert "private budget" not in raw
    assert await store.load(identity, "model-a", "response-a") == record
    assert await store.load(identity, "model-b", "response-a") is None
    assert await store.load(identity.model_copy(update={"key_hash": "key-b"}), "model-a", "response-a") is None
    await store.save(identity, "model-a", "expired", record.model_copy(update={"expires_at": time.time() - 1}))
    assert await store.load(identity, "model-a", "expired") is None
    await store.save(
        identity,
        "model-a",
        "oversize",
        record.model_copy(
            update={
                "input": ({"role": "user", "content": "x" * 300000},),
            }
        ),
    )
    assert await store.load(identity, "model-a", "oversize") is None
    await cache.async_set_cache(store.key(identity, "model-a", "response-a"), raw[:-8] + "AAAAAAAA")
    assert await store.load(identity, "model-a", "response-a") is None


@pytest.mark.asyncio
async def test_stream_error_after_output_is_not_replayed_or_saved(pool):
    from litellm.responses.streaming_iterator import BaseResponsesAPIStreamingIterator
    from litellm.types.llms.openai import OutputTextDeltaEvent

    router, (first, _) = pool
    attempts = []

    class Stream(BaseResponsesAPIStreamingIterator):
        def __init__(self):
            self.emitted = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.emitted:
                raise RateLimitError(message="quota_exhausted", model="shared", llm_provider="openai")
            self.emitted = True
            return OutputTextDeltaEvent(
                type="response.output_text.delta", delta="partial", item_id="msg_a", output_index=0, content_index=0
            )

    async def provider(**kwargs):
        attempts.append(chosen(kwargs))
        return Stream()

    stream = await router._aresponses_with_streaming_fallbacks(
        original_function=provider, model="shared", input="Budget 5000", stream=True
    )
    assert (await anext(stream)).delta == "partial"
    with pytest.raises(RateLimitError):
        await anext(stream)
    assert attempts == [str(first)]

@pytest.mark.asyncio
@pytest.mark.parametrize("remaining", [None, 50])
async def test_pre_call_unavailable_origin_rebuilds_with_nonempty_quota(pool, remaining):
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation
    from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import EncryptedContentAffinityCheck
    router, (first, second) = pool
    snapshots.replace({str(card): RoutingSnapshot(fallback_enabled=True, quota=RoutingQuota(remaining_percent=remaining)) for card in (first, second)})
    encrypted = ResponsesAPIRequestUtils._wrap_encrypted_content_with_model_id("opaque", str(first))
    payload = {"model": "shared", "store": False, "input": [{"role": "user", "content": "Budget 5000"}, {"type": "reasoning", "encrypted_content": encrypted}, {"role": "assistant", "content": "For what?"}, {"role": "user", "content": "Editing"}]}
    conversation = await Conversation.start(router, payload)
    error = await EncryptedContentAffinityCheck(router)._unavailable_origin_error("shared", str(first), router.get_deployment(str(first)), None)
    rebuilt = conversation.rebuild(router, payload, error)
    assert rebuilt is not None
    assert rebuilt["input"] == [payload["input"][0], payload["input"][2], payload["input"][3]]
    assert str(first) in rebuilt["_excluded_deployment_ids"]

@pytest.mark.asyncio
async def test_rebuilt_second_card_failure_reaches_third_with_same_context(pool):
    import httpx

    from litellm.exceptions import ServiceUnavailableError
    router, (first, second) = pool
    third = uuid4()
    from litellm.types.router import Deployment
    router.add_deployment(Deployment(model_name="shared", model_info={"id": str(third), "account_pool_environment_id": str(third)}, litellm_params={"model": "openai/test-model", "api_key": "test", "order": 2}))
    snapshots.replace({str(card): RoutingSnapshot(fallback_enabled=True) for card in (first, second, third)})
    calls = []
    async def provider(**kwargs):
        card = chosen(kwargs)
        calls.append(card)
        if card != str(third):
            raise ServiceUnavailableError(message="unavailable", model="shared", llm_provider="openai", response=httpx.Response(503, headers={"x-account-pool-failure": "upstream_unavailable", "x-account-pool-rebuild": "retry-exhausted", "x-account-pool-attempt": "1"}, request=httpx.Request("POST", "http://local")))
        assert kwargs["input"] == [{"role": "user", "content": "Budget 5000"}, {"role": "user", "content": "Editing"}]
        return response(third)
    result = await router._aresponses_with_streaming_fallbacks(original_function=provider, model="shared", input=[{"role": "user", "content": "Budget 5000"}, {"type": "reasoning", "encrypted_content": "opaque"}, {"role": "user", "content": "Editing"}])
    assert result.status == "completed"
    assert calls == [str(first), str(second), str(third)]

@pytest.mark.asyncio
async def test_mixed_origin_reasoning_preserves_selected_boundary_and_readable_summary(pool):
    router, (first, second) = pool
    from litellm.proxy.management_endpoints.account_pool_conversation import compatible_reasoning_input
    first_state = ResponsesAPIRequestUtils._wrap_encrypted_content_with_model_id("a", str(first))
    second_state = ResponsesAPIRequestUtils._wrap_encrypted_content_with_model_id("b", str(second))
    history = [{"role": "user", "content": "hello"}, {"type": "reasoning", "encrypted_content": first_state}, {"type": "reasoning", "id": "old", "encrypted_content": second_state, "summary": [{"type": "summary_text", "text": "Visible summary"}]}, {"role": "assistant", "content": "Hello"}, {"role": "user", "content": "continue"}]
    result = compatible_reasoning_input(router, {"input": history}, str(first))
    assert result[1] == history[1]
    assert result[2] == {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Visible summary"}]}
    assert history[2]["encrypted_content"] == second_state

@pytest.mark.asyncio
async def test_mixed_reasoning_is_cleaned_at_real_router_dispatch(pool):
    router, (first, second) = pool
    first_state = ResponsesAPIRequestUtils._wrap_encrypted_content_with_model_id("a", str(first))
    second_state = ResponsesAPIRequestUtils._wrap_encrypted_content_with_model_id("b", str(second))
    async def provider(**kwargs):
        assert chosen(kwargs) == str(first)
        assert len(kwargs["input"]) == 2
        assert kwargs["input"][1]["encrypted_content"] == first_state
        return response(first)
    result = await router._aresponses_with_streaming_fallbacks(original_function=provider, model="shared", input=[{"role": "user", "content": "hello"}, {"type": "reasoning", "encrypted_content": first_state}, {"type": "reasoning", "encrypted_content": second_state}])
    assert result.status == "completed"

@pytest.fixture
def degraded(pool, monkeypatch):
    monkeypatch.setenv("ACCOUNT_POOL_DEGRADED_CONTINUATION", "1")
    return pool

@pytest.mark.asyncio
async def test_full_history_replays_without_encrypted_reasoning(degraded):
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation
    from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import (
        EncryptedContentOriginUnavailableError,
    )

    router, (first, second) = degraded
    payload = {
        "model": "shared",
        "input": [
            {"type": "message", "role": "user", "content": "Budget 5000"},
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "plan"}], "encrypted_content": "gAAAA"},
            {"type": "message", "role": "assistant", "content": "ok"},
            {"type": "message", "role": "user", "content": "Continue"},
        ],
    }
    conversation = await Conversation.start(router, payload)
    assert conversation is not None
    calls = []

    async def invoke(value):
        calls.append(value)

    error = EncryptedContentOriginUnavailableError(message="origin unavailable", model="shared", llm_provider="openai")
    error.failed_deployment_id = str(first)
    await conversation.recover(router, payload, error, invoke)

    assert len(calls) == 1
    rebuilt = calls[0]
    assert "previous_response_id" not in rebuilt
    assert all("encrypted_content" not in item for item in rebuilt["input"])
    assert any(item.get("role") == "assistant" for item in rebuilt["input"])

@pytest.mark.asyncio
async def test_partial_history_does_not_replay_only_the_visible_turn(degraded):
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation
    from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import (
        EncryptedContentOriginUnavailableError,
    )

    router, _ = degraded
    payload = {"model": "shared", "input": "Continue", "previous_response_id": "resp_unknown"}
    conversation = await Conversation.start(router, payload)
    assert conversation is not None
    calls = []

    async def invoke(value):
        calls.append(value)

    with pytest.raises(ConversationUnavailableError):
        await conversation.recover(router, payload, EncryptedContentOriginUnavailableError(message="gone", model="shared", llm_provider="openai"), invoke)
    assert calls == []

@pytest.mark.asyncio
async def test_generic_server_error_does_not_degrade(degraded):
    from litellm.exceptions import InternalServerError
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation

    router, _ = degraded
    payload = {"model": "shared", "input": "Continue", "previous_response_id": "resp_unknown"}
    conversation = await Conversation.start(router, payload)
    assert conversation is not None
    calls = []

    async def invoke(value):
        calls.append(value)

    error = InternalServerError(message="boom", model="shared", llm_provider="openai")
    with pytest.raises(InternalServerError):
        await conversation.recover(router, payload, error, invoke)
    assert calls == []

@pytest.mark.asyncio
async def test_card_bound_key_keeps_switch_gate(degraded, monkeypatch):
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation
    from litellm.proxy.management_endpoints.account_pool_integration import PoolIdentity, pool_identity

    router, cards = degraded
    snapshots.replace({str(card): RoutingSnapshot() for card in cards})
    token = pool_identity.set(PoolIdentity(key_hash="bound", request_id=uuid4(), card_id=uuid4()))
    try:
        assert await Conversation.start(router, {"model": "shared", "input": "hello"}) is None
    finally:
        pool_identity.reset(token)

@pytest.mark.asyncio
async def test_quota_depleted_snapshot_degrades_full_history(degraded):
    import httpx

    from litellm.exceptions import ServiceUnavailableError
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation

    router, (first, second) = degraded
    snapshots.replace(
        {
            str(first): RoutingSnapshot(quota_switch_project=True, quota=RoutingQuota(remaining_percent=0)),
            str(second): RoutingSnapshot(quota_switch_project=True),
        }
    )
    payload = {
        "model": "shared",
        "input": [
            {"type": "message", "role": "user", "content": "first"},
            {"type": "message", "role": "assistant", "content": "second"},
            {"type": "message", "role": "user", "content": "third"},
        ],
    }
    conversation = await Conversation.start(router, payload)
    assert conversation is not None
    calls = []

    async def invoke(value):
        calls.append(value)

    error = ServiceUnavailableError(
        message="quota exhausted",
        model="shared",
        llm_provider="openai",
        response=httpx.Response(503, request=httpx.Request("POST", "http://local/v1/responses")),
    )
    error.failed_deployment_id = str(first)
    await conversation.recover(router, payload, error, invoke)

    assert len(calls) == 1
    assert "previous_response_id" not in calls[0]

@pytest.mark.asyncio
async def test_plain_quota_503_degrades_before_output(degraded):
    import httpx

    from litellm.exceptions import ServiceUnavailableError
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation

    router, (first, second) = degraded
    payload = {
        "model": "shared",
        "input": [
            {"type": "message", "role": "user", "content": "first"},
            {"type": "message", "role": "assistant", "content": "second"},
            {"type": "message", "role": "user", "content": "third"},
        ],
    }
    conversation = await Conversation.start(router, payload)
    assert conversation is not None
    calls = []

    async def invoke(value):
        calls.append(value)

    error = ServiceUnavailableError(
        message="quota exhausted",
        model="shared",
        llm_provider="openai",
        response=httpx.Response(
            503,
            headers={"x-account-pool-failure": "quota_exhausted"},
            request=httpx.Request("POST", "http://local/v1/responses"),
        ),
    )
    error.failed_deployment_id = str(first)
    await conversation.recover(router, payload, error, invoke)

    assert len(calls) == 1
    assert "previous_response_id" not in calls[0]

@pytest.mark.asyncio
async def test_dangling_tool_call_blocks_replay_without_dropping_history(degraded):
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation
    from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import (
        EncryptedContentOriginUnavailableError,
    )

    router, (first, second) = degraded
    payload = {
        "model": "shared",
        "input": [
            {"type": "message", "role": "user", "content": "first"},
            {"type": "message", "role": "assistant", "content": "second"},
            {"type": "function_call", "call_id": "call-1", "name": "tool", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call-1", "output": "ok"},
            {"type": "function_call", "call_id": "call-2", "name": "tool", "arguments": "{}"},
            {"type": "message", "role": "user", "content": "third"},
        ],
    }
    conversation = await Conversation.start(router, payload)
    assert conversation is not None
    calls = []

    async def invoke(value):
        calls.append(value)

    error = EncryptedContentOriginUnavailableError(message="origin unavailable", model="shared", llm_provider="openai")
    error.failed_deployment_id = str(first)
    with pytest.raises(ConversationUnavailableError):
        await conversation.recover(router, payload, error, invoke)
    assert calls == []

@pytest.mark.asyncio
async def test_extra_body_does_not_block_relaxed_start(degraded):
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation

    router, _ = degraded
    payload = {"model": "shared", "input": "hi", "extra_body": {"include": ["reasoning.encrypted_content"]}}
    assert await Conversation.start(router, payload) is not None

@pytest.mark.asyncio
async def test_extra_body_still_blocks_when_disabled(pool):
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation

    router, _ = pool
    payload = {"model": "shared", "input": "hi", "extra_body": {"include": ["reasoning.encrypted_content"]}}
    assert await Conversation.start(router, payload) is None

@pytest.mark.asyncio
async def test_extra_body_is_preserved_in_replay(degraded):
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation
    from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import (
        EncryptedContentOriginUnavailableError,
    )

    router, (first, second) = degraded
    payload = {
        "model": "shared",
        "extra_body": {"include": ["reasoning.encrypted_content"]},
        "input": [
            {"type": "message", "role": "user", "content": "first"},
            {"type": "message", "role": "assistant", "content": "second"},
            {"type": "message", "role": "user", "content": "third"},
        ],
    }
    conversation = await Conversation.start(router, payload)
    assert conversation is not None
    calls = []

    async def invoke(value):
        calls.append(value)

    error = EncryptedContentOriginUnavailableError(message="origin unavailable", model="shared", llm_provider="openai")
    error.failed_deployment_id = str(first)
    await conversation.recover(router, payload, error, invoke)

    assert calls[0]["extra_body"] == {"include": ["reasoning.encrypted_content"]}

@pytest.mark.asyncio
async def test_private_item_reference_blocks_replay_without_dropping_history(degraded):
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation
    from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import (
        EncryptedContentOriginUnavailableError,
    )

    router, (first, second) = degraded
    payload = {
        "model": "shared",
        "input": [
            {"type": "item_reference", "id": "unsupported"},
            {"type": "message", "role": "user", "content": "hi"},
        ],
    }
    conversation = await Conversation.start(router, payload)
    assert conversation is not None
    calls = []

    async def invoke(value):
        calls.append(value)

    error = EncryptedContentOriginUnavailableError(message="origin unavailable", model="shared", llm_provider="openai")
    error.failed_deployment_id = str(first)
    with pytest.raises(ConversationUnavailableError):
        await conversation.recover(router, payload, error, invoke)
    assert calls == []

@pytest.mark.asyncio
async def test_preempt_switches_fresh_depleted_origin_with_full_history(degraded):
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation
    from litellm.proxy.management_endpoints.account_pool_native_routing import RoutingQuota, now_utc

    router, (first, second) = degraded
    initial = await Conversation.start(router, {"model": "shared", "input": [{"type": "message", "role": "user", "content": "first"}]})
    parent = response(first, text="second")
    await initial.complete(parent)
    snapshots.replace(
        {
            str(first): RoutingSnapshot(quota_switch_project=True, quota=RoutingQuota(remaining_percent=0, observed_at=now_utc())),
            str(second): RoutingSnapshot(quota_switch_project=True, quota=RoutingQuota(remaining_percent=100, observed_at=now_utc())),
        }
    )
    payload = {
        "model": "shared",
        "previous_response_id": parent.id,
        "input": [
            {"type": "message", "role": "user", "content": "first"},
            {"type": "message", "role": "assistant", "content": "second"},
            {"type": "message", "role": "user", "content": "third"},
        ],
    }
    conversation = await Conversation.start(router, payload)
    assert conversation is not None
    preempted = conversation.preempt(router, payload)
    assert preempted is not None
    assert "previous_response_id" not in preempted
    assert len(preempted["input"]) == 3
    assert str(first) in preempted["_excluded_deployment_ids"]
    assert "x-account-pool-degraded-continuation" not in (preempted.get("headers") or {})

@pytest.mark.asyncio
async def test_preempt_does_not_switch_with_partial_history(degraded):
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation
    from litellm.proxy.management_endpoints.account_pool_native_routing import RoutingQuota, now_utc

    router, (first, second) = degraded
    snapshots.replace(
        {
            str(first): RoutingSnapshot(quota_switch_project=True, quota=RoutingQuota(remaining_percent=0, observed_at=now_utc())),
            str(second): RoutingSnapshot(quota_switch_project=True, quota=RoutingQuota(remaining_percent=100, observed_at=now_utc())),
        }
    )
    payload = {"model": "shared", "previous_response_id": response(first).id, "input": "Continue"}
    conversation = await Conversation.start(router, payload)
    assert conversation is not None
    with pytest.raises(ConversationUnavailableError):
        conversation.preempt(router, payload)

@pytest.mark.asyncio
async def test_preempt_switches_known_depleted_stale_snapshot(degraded):
    from datetime import timedelta

    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation
    from litellm.proxy.management_endpoints.account_pool_native_routing import RoutingQuota, now_utc

    router, (first, second) = degraded
    initial = await Conversation.start(router, {"model": "shared", "input": [{"role": "user", "content": "Original"}]})
    parent = response(first, text="Answer")
    await initial.complete(parent)
    snapshots.replace(
        {
            str(first): RoutingSnapshot(quota_switch_project=True, quota=RoutingQuota(remaining_percent=0, observed_at=now_utc() - timedelta(hours=2))),
            str(second): RoutingSnapshot(quota_switch_project=True),
        }
    )
    payload = {"model": "shared", "previous_response_id": parent.id, "input": [{"role": "user", "content": "Original"}, {"role": "assistant", "content": "Answer"}, {"role": "user", "content": "Continue"}]}
    conversation = await Conversation.start(router, payload)
    assert conversation is not None
    assert conversation.preempt(router, payload) is not None

@pytest.mark.asyncio
async def test_router_preempts_before_calling_depleted_origin(degraded):
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation
    from litellm.proxy.management_endpoints.account_pool_native_routing import RoutingQuota, now_utc

    router, (first, second) = degraded
    initial = await Conversation.start(router, {"model": "shared", "input": [{"role": "user", "content": "Original"}]})
    parent = response(first, text="Answer")
    await initial.complete(parent)
    snapshots.replace(
        {
            str(first): RoutingSnapshot(quota_switch_project=True, quota=RoutingQuota(remaining_percent=0, observed_at=now_utc())),
            str(second): RoutingSnapshot(quota_switch_project=True, quota=RoutingQuota(remaining_percent=100, observed_at=now_utc())),
        }
    )
    calls = []

    async def provider(**kwargs):
        card = chosen(kwargs)
        calls.append(card)
        return response(card)

    await router._aresponses_with_streaming_fallbacks(
        original_function=provider,
        model="shared",
        input=[{"role": "user", "content": "Original"}, {"role": "assistant", "content": "Answer"}, {"role": "user", "content": "Continue"}],
        previous_response_id=parent.id,
    )

    assert calls == [str(second)]

@pytest.mark.asyncio
async def test_signed_session_unavailable_replays_visible_history(degraded):
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation
    from litellm.proxy.management_endpoints.account_pool_session import AccountPoolSessionUnavailableError

    router, (first, second) = degraded
    payload = {
        "model": "shared",
        "input": [
            {"type": "message", "role": "user", "content": "first"},
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "plan"}], "signature": "signed"},
            {"type": "message", "role": "assistant", "content": "second"},
            {"type": "message", "role": "user", "content": "third"},
        ],
    }
    conversation = await Conversation.start(router, payload)
    assert conversation is not None
    calls = []

    async def invoke(value):
        calls.append(value)

    error = AccountPoolSessionUnavailableError(message="signed state unavailable", model="shared", llm_provider="account_pool")
    error.failed_deployment_id = str(first)
    await conversation.recover(router, payload, error, invoke)

    assert len(calls) == 1
    assert "previous_response_id" not in calls[0]
    assert calls[0]["litellm_metadata"]["account_pool_signed_continuation"] is False
    assert all("signature" not in item for item in calls[0]["input"])

def test_missing_image_result_does_not_silently_drop_history() -> None:
    from litellm.proxy.management_endpoints.account_pool_conversation import visible_input
    history = [{"role": "user", "content": "first"}, {"type": "image_generation_call", "id": "ig-1"}]
    assert visible_input(history, lenient=True) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("window", [300, 10080])
@pytest.mark.parametrize("preemptive", [True, False])
async def test_quota_migration_preserves_image_tools_and_next_turn(degraded, window, preemptive):
    from datetime import timedelta

    from litellm.proxy.management_endpoints.account_pool_native_routing import RoutingQuotaWindow, now_utc
    router, (first, second) = degraded
    png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
    history = [
        {"role": "user", "content": "Draw a dot then look up its name"},
        {"type": "reasoning", "encrypted_content": ResponsesAPIRequestUtils._wrap_encrypted_content_with_model_id("old", str(first)), "summary": [{"type": "summary_text", "text": "Draw then check"}]},
        {"type": "image_generation_call", "id": "ig_original", "status": "completed", "result": png},
        {"type": "function_call", "call_id": "call_one", "name": "lookup", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_one", "output": "dot"},
        {"role": "user", "content": "Continue with that image"},
    ]
    original = copy.deepcopy(history)
    now = now_utc()
    if preemptive:
        snapshots.replace({
            str(first): RoutingSnapshot(quota=RoutingQuota(observed_at=now - timedelta(hours=2), windows=(RoutingQuotaWindow(window_minutes=window, remaining_percent=2, resets_at=now+timedelta(hours=1)),)), quota_reserve_5h_percent=2, quota_reserve_weekly_percent=2),
            str(second): RoutingSnapshot(quota=RoutingQuota(remaining_percent=80, observed_at=now)),
        })
    calls = []
    async def provider(**kwargs):
        card = chosen(kwargs)
        calls.append((card, copy.deepcopy(kwargs)))
        if card == str(first):
            raise RateLimitError(message="quota_exhausted", model="shared", llm_provider="openai")
        return response(second, "The saved dot is available")
    tools = [{"type": "function", "name": "lookup", "parameters": {"type": "object"}}]
    result = await router._aresponses_with_streaming_fallbacks(original_function=provider, model="shared", input=history, instructions="Keep the original picture", tools=tools)
    assert result.status == "completed"
    assert [card for card, _ in calls] == ([str(second)] if preemptive else [str(first), str(second)])
    received = calls[-1][1]
    assert received["instructions"] == "Keep the original picture"
    assert received["tools"] == tools
    assert original == history
    assert received["input"][-3:] == original[-3:]
    assert all("encrypted_content" not in item for item in received["input"])
    assert any(block.get("image_url") == "data:image/png;base64," + png for item in received["input"] for block in item.get("content", []) if isinstance(block, dict))
    assert any(item.get("summary") == original[1]["summary"] for item in received["input"])
    await router._aresponses_with_streaming_fallbacks(original_function=provider, model="shared", input="Next", previous_response_id=result.id)
    assert calls[-1][0] == str(second)
    assert calls[-1][1]["previous_response_id"] == result.id


@pytest.mark.asyncio
async def test_cached_instructions_and_tools_survive_quota_migration(degraded):
    router, (first, second) = degraded
    calls = []
    async def provider(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        if len(calls) == 1:
            return response(first, output=[{"type": "function_call", "call_id": "lookup_1", "name": "lookup", "arguments": "{}"}])
        if chosen(kwargs) == str(first):
            raise RateLimitError(message="quota_exhausted", model="shared", llm_provider="openai")
        return response(second)
    tools = [{"type": "function", "name": "lookup", "parameters": {"type": "object"}}]
    initial = await router._aresponses_with_streaming_fallbacks(original_function=provider, model="shared", input="Look up a dot", instructions="Keep all results", tools=tools)
    await router._aresponses_with_streaming_fallbacks(original_function=provider, model="shared", previous_response_id=initial.id, input=[{"type": "function_call_output", "call_id": "lookup_1", "output": "dot"}])
    assert chosen(calls[-1]) == str(second)
    assert calls[-1].get("instructions") == "Keep all results"
    assert calls[-1].get("tools") == tools


@pytest.mark.asyncio
async def test_relaxed_recovery_does_not_reclassify_invalid_prompt(degraded):
    from litellm.exceptions import BadRequestError
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation
    router, (first, second) = degraded
    snapshots.replace({str(first): RoutingSnapshot(quota=RoutingQuota(remaining_percent=0)), str(second): RoutingSnapshot()})
    payload = {"model": "shared", "input": [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi"}]}
    conversation = await Conversation.start(router, payload)
    error = BadRequestError(message="invalid_prompt", model="shared", llm_provider="openai")
    error.failed_deployment_id = str(first)
    assert conversation.rebuild(router, payload, error) is None


@pytest.mark.asyncio
async def test_extra_body_server_state_is_not_replayed(degraded):
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation
    router, _ = degraded
    payload = {"model": "shared", "input": "Hello", "extra_body": {"previous_response_id": "private_state"}}
    conversation = await Conversation.start(router, payload)
    assert not conversation.can_replay(router, payload)


@pytest.mark.asyncio
async def test_old_cache_with_unsupported_item_cannot_migrate_as_complete_history(degraded):
    import time

    from litellm.proxy.management_endpoints.account_pool_conversation import (
        Conversation,
        ConversationRecord,
        ConversationStore,
    )
    router, (first, _) = degraded
    identity = pool_identity.get()
    store = ConversationStore(router.cache, "test-conversation-secret-" * 2)
    previous = response(first).id
    await store.save(identity, "shared", previous, ConversationRecord(input=({"role": "user", "content": "hello"}, {"type": "image_generation_call", "id": "private"}), deployment_id=str(first), expires_at=time.time()+100))
    conversation = await Conversation.start(router, {"model": "shared", "input": "Next", "previous_response_id": previous})
    assert not conversation.can_replay(router, {})


@pytest.mark.asyncio
async def test_stream_quota_error_before_output_migrates_complete_history(degraded):
    from litellm.responses.streaming_iterator import BaseResponsesAPIStreamingIterator
    from litellm.types.llms.openai import OutputTextDeltaEvent
    router, (first, second) = degraded
    attempts = []
    class Stream(BaseResponsesAPIStreamingIterator):
        def __init__(self, card):
            self.card = card
            self._hidden_params = {"model_id": card}
            self.closed = False
            self.sent = False
        def __aiter__(self):
            return self
        async def __anext__(self):
            if self.card == str(first):
                raise RateLimitError(message="quota_exhausted", model="shared", llm_provider="openai")
            if self.sent:
                raise StopAsyncIteration
            self.sent = True
            return OutputTextDeltaEvent(type="response.output_text.delta", delta="continued", item_id="m", output_index=0, content_index=0)
        async def aclose(self):
            self.closed = True
    async def provider(**kwargs):
        card = chosen(kwargs)
        attempts.append(card)
        if card == str(second):
            assert kwargs["input"] == [{"role": "user", "content": "Keep this history"}]
        return Stream(card)
    stream = await router._aresponses_with_streaming_fallbacks(original_function=provider, model="shared", input="Keep this history", stream=True)
    assert (await anext(stream)).delta == "continued"
    assert attempts == [str(first), str(second)]


@pytest.mark.asyncio
async def test_old_cache_image_is_normalized_before_replay(degraded):
    import time

    from litellm.proxy.management_endpoints.account_pool_conversation import (
        Conversation,
        ConversationRecord,
        ConversationStore,
    )
    router, (first, second) = degraded
    png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
    previous = response(first).id
    store = ConversationStore(router.cache, "test-conversation-secret-" * 2)
    await store.save(pool_identity.get(), "shared", previous, ConversationRecord(input=({"role": "user", "content": "Draw"}, {"type": "image_generation_call", "id": "ig_old", "status": "completed", "result": png}), deployment_id=str(first), expires_at=time.time()+100))
    payload = {"model": "shared", "input": "Continue", "previous_response_id": previous}
    conversation = await Conversation.start(router, payload)
    error = RateLimitError(message="quota_exhausted", model="shared", llm_provider="openai")
    error.failed_deployment_id = str(first)
    rebuilt = conversation.rebuild(router, payload, error)
    assert not any(item.get("type") == "image_generation_call" for item in rebuilt["input"])
    assert any(block.get("image_url") == "data:image/png;base64,"+png for item in rebuilt["input"] for block in item.get("content", []) if isinstance(block, dict))


@pytest.mark.asyncio
async def test_migration_preserves_opaque_logging_metadata(degraded):
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation
    router, (first, _) = degraded
    logging_object = object()
    payload = {"model": "shared", "input": "Hello", "litellm_metadata": {"logging_object": logging_object}}
    conversation = await Conversation.start(router, payload)
    error = RateLimitError(message="quota_exhausted", model="shared", llm_provider="openai")
    error.failed_deployment_id = str(first)
    rebuilt = conversation.rebuild(router, payload, error)
    assert rebuilt["litellm_metadata"]["logging_object"] is logging_object
    assert "account_pool_signed_continuation" not in payload["litellm_metadata"]


@pytest.mark.asyncio
async def test_stream_preparation_cancellation_closes_source(degraded, monkeypatch):
    import asyncio

    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation, ConversationStream
    monkeypatch.delattr(asyncio, "timeout", raising=False)
    router, _ = degraded
    conversation = await Conversation.start(router, {"model": "shared", "input": "Hello"})
    class Stream:
        closed = False
        def __aiter__(self):
            return self
        async def __anext__(self):
            raise asyncio.CancelledError()
        async def aclose(self):
            self.closed = True
    source = Stream()
    with pytest.raises(asyncio.CancelledError):
        await ConversationStream(source, conversation).prepare()
    assert source.closed


@pytest.mark.asyncio
async def test_large_inline_image_migrates_but_is_not_claimed_as_cached(degraded):
    import base64
    import random
    import struct
    import zlib

    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation
    router, (first, second) = degraded
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    pixels = random.Random(0).randbytes(512 * 512 * 3)
    scanlines = b"".join(b"\0" + pixels[i:i + 1536] for i in range(0, len(pixels), 1536))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 512, 512, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b"")
    encoded = base64.b64encode(png).decode()
    assert len(encoded) > 256 * 1024
    payload = {"model": "shared", "input": [{"role": "user", "content": "Draw"}, {"type": "image_generation_call", "id": "large", "status": "completed", "result": encoded}]}
    conversation = await Conversation.start(router, payload)
    error = RateLimitError(message="quota_exhausted", model="shared", llm_provider="openai")
    error.failed_deployment_id = str(first)
    rebuilt = conversation.rebuild(router, payload, error)
    assert rebuilt["input"][1]["content"][1]["image_url"] == "data:image/png;base64," + encoded
    completed = response(second)
    await conversation.complete(completed)
    next_turn = await Conversation.start(router, {"model": "shared", "input": "Next", "previous_response_id": completed.id})
    assert not next_turn.can_replay(router, {})
@pytest.mark.asyncio
@pytest.mark.parametrize("full_replay", [False, True])
async def test_reconnect_history_prefix_is_not_duplicated(degraded, full_replay):
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation
    router, (first, _) = degraded
    initial_items = [{"role": "user", "content": "1"}, {"role": "assistant", "content": "2"}]
    initial = await Conversation.start(router, {"model": "shared", "input": initial_items})
    parent = response(first, text="3")
    await initial.complete(parent)
    current = [*initial_items, {"type": "message", "role": "assistant", "content": "3"}] if full_replay else []
    current.append({"role": "user", "content": "4"})
    continued = await Conversation.start(router, {"model": "shared", "previous_response_id": parent.id, "input": current})
    assert [item["content"] for item in continued.history] == ["1", "2", "3", "4"]


@pytest.mark.parametrize("entry", [
    {"type": "additional_tools", "tools": [{"type": "function", "name": "lookup"}]},
    {"type": "agent_message", "content": [{"type": "input_text", "text": "Visible handoff"}]},
    {"type": "configuration_update", "reasoning": {"effort": "high"}},
    {"type": "compaction_trigger"},
    {"type": "function_call_output", "call_id": "c1", "output": [{"type": "input_text", "text": "result"}]},
])
def test_codex_visible_history_portability(entry):
    from litellm.proxy.management_endpoints.account_pool_conversation import visible_input
    assert visible_input([{"role": "user", "content": "Continue"}, entry]) is not None


@pytest.mark.parametrize("cached,current,expected", [
    ([{"role": "user", "content": "1"}], [{"role": "user", "content": "1"}], ["1", "1"]),
    ([{"role": "user", "content": str(i)} for i in range(1, 4)], [{"role": "user", "content": "1"}], ["1", "2", "3", "1"]),
])
def test_history_merge_preserves_deliberate_repeated_delta(cached, current, expected):
    from litellm.litellm_core_utils.signature_recovery import merge_visible_history
    assert [item["content"] for item in merge_visible_history(tuple(cached), tuple(current))] == expected


@pytest.mark.parametrize(
    "current,expected",
    [
        ([{"role": "user", "content": "1"}, {"role": "user", "content": "2"}], ["1", "2"]),
        ([{"role": "user", "content": "1"}], ["1", "1"]),
    ],
)
def test_single_item_reconnect_overlap_does_not_remove_a_repeated_delta(current, expected):
    from litellm.litellm_core_utils.signature_recovery import merge_visible_history

    cached = ({"role": "user", "content": "1"},)
    assert [item["content"] for item in merge_visible_history(cached, tuple(current))] == expected


@pytest.mark.asyncio
async def test_missing_parent_with_assistant_is_not_complete(degraded):
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation
    router, (first, _) = degraded
    payload = {"model": "shared", "previous_response_id": response(first).id, "input": [
        {"role": "user", "content": "Recent context"}, {"role": "assistant", "content": "Recent reply"},
        {"role": "user", "content": "Next"},
    ]}
    conversation = await Conversation.start(router, payload)
    assert conversation.history is None
    assert not conversation.can_replay(router, payload)


def test_web_search_declaration_does_not_block_visible_client_history():
    from litellm.litellm_core_utils.signature_recovery import signature_recovery_reason
    assert signature_recovery_reason({"input": [{"role": "user", "content": "Hello"}], "tools": [{"type": "web_search"}]}) is None
    assert signature_recovery_reason({"input": [{"role": "user", "content": "Hello"}, {"type": "web_search_call", "id": "search"}]}) is not None


@pytest.mark.parametrize("output", [
    [{"type": "input_text", "text": "contents"}],
    [{"type": "input_image", "image_url": "data:image/png;base64,eA=="}],
    [{"type": "input_file", "filename": "readme.txt", "file_data": "data:text/plain;base64,eA=="}],
])
def test_visible_tool_output_arrays_preserve_tool_identity(output):
    from litellm.litellm_core_utils.signature_recovery import signature_recovery_reason
    from litellm.proxy.management_endpoints.account_pool_conversation import visible_input
    history = [{"role": "user", "content": "Read"},
               {"type": "function_call", "id": "tool-item", "call_id": "c1", "name": "read", "arguments": "{}"},
               {"type": "function_call_output", "call_id": "c1", "output": output}]
    visible = visible_input(history)
    assert visible is not None
    assert [dict(item) for item in visible] == history
    assert signature_recovery_reason({"input": history}) is None


def test_visible_history_rejects_unknown_items_without_dropping_them():
    from litellm.proxy.management_endpoints.account_pool_conversation import visible_input
    assert visible_input([{"role": "user", "content": "Keep"}, {"type": "unknown_server_action", "payload": "do not drop"}]) is None


def test_visible_agent_message_keeps_text_without_opaque_ciphertext():
    from litellm.proxy.management_endpoints.account_pool_conversation import visible_input
    agent = {"type": "agent_message", "content": [
        {"type": "input_text", "text": "Visible handoff"},
        {"type": "encrypted_content", "encrypted_content": "old opaque ciphertext"},
    ]}
    visible = visible_input([{"role": "user", "content": "Keep"}, agent])
    assert visible[1]["content"] == [{"type": "input_text", "text": "Visible handoff"}]


@pytest.mark.asyncio
async def test_compaction_can_rebuild_only_with_verified_complete_parent(degraded):
    from litellm.proxy.management_endpoints.account_pool_conversation import Conversation
    router, (first, _) = degraded
    initial = await Conversation.start(router, {"model": "shared", "input": "Original context"})
    parent = response(first, text="Original reply")
    await initial.complete(parent)
    current = [{"type": "compaction", "encrypted_content": "opaque"}, {"role": "user", "content": "Next"}]
    verified = await Conversation.start(router, {"model": "shared", "previous_response_id": parent.id, "input": current})
    assert verified.can_replay(router, {})
    assert [item["content"] for item in verified.history] == ["Original context", "Original reply", "Next"]
    missing = await Conversation.start(router, {"model": "shared", "previous_response_id": response(first).id, "input": current})
    assert not missing.can_replay(router, {})
