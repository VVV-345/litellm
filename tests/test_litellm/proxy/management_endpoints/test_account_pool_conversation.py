import copy
from uuid import uuid4

import pytest

from litellm import Router
from litellm.exceptions import RateLimitError
from litellm.proxy.management_endpoints.account_pool_integration import PoolIdentity, pool_identity
from litellm.proxy.management_endpoints.account_pool_native_routing import RoutingQuota, RoutingSnapshot, snapshots
from litellm.responses.utils import ResponsesAPIRequestUtils
from litellm.types.llms.openai import ResponsesAPIResponse


@pytest.fixture
def pool(monkeypatch):
    monkeypatch.setenv("ACCOUNT_POOL_MANAGER_TOKEN", "test-conversation-secret-" * 2)
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
        {"tools": [{"type": "web_search"}]},
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
