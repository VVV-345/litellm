"""验证统一入口的卡片范围、内部身份签名和标准记账边界。"""

from __future__ import annotations

import asyncio
import queue
import socket
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from litellm import Router
from litellm.proxy.management_endpoints.account_pool_gateway import AccountPoolGatewayMiddleware
from litellm.proxy.management_endpoints.account_pool_gateway_contracts import ResolveRequest
from litellm.proxy.management_endpoints.account_pool_integration import (
    INTERNAL_PREFIX,
    CardScope,
    PoolIdentity,
    completed_pool_metadata,
    create_ticket,
    pool_identity,
    register_card_key,
    verify_ticket,
)
from tests.test_litellm.proxy.management_endpoints.test_account_pool_gateway import (
    Control,
    FakeWebSocketDialer,
    setup_gateway,
)


class InternalControl(Control):
    async def resolve(self, request: ResolveRequest):
        assert request.card_key == ""
        assert request.trusted_card_id == self.resolution.card_id
        assert request.trusted_key_id is not None
        return self.resolution


@pytest.fixture
def signing_secret(monkeypatch):
    monkeypatch.setenv("ACCOUNT_POOL_MANAGER_TOKEN", "test-only-internal-secret-" * 3)


def test_ticket_rejects_tampering_and_cross_card(signing_secret):
    card = uuid4()
    identity = PoolIdentity(key_hash="test-hash", request_id=uuid4(), card_id=card)
    ticket = create_ticket(identity, card)
    assert verify_ticket(ticket, card).identity == identity
    with pytest.raises(HTTPException):
        verify_ticket(ticket + "0", card)
    with pytest.raises(HTTPException):
        verify_ticket(ticket, uuid4())
    other = uuid4()
    with pytest.raises(HTTPException):
        verify_ticket(create_ticket(identity, other), other)


def test_final_account_attribution_requires_matching_internal_request():
    request_id, card_id, fallback_id = uuid4(), uuid4(), uuid4()
    metadata = {"account_pool_request_id": str(request_id), "account_pool_card_id": str(card_id), "project": "test"}
    headers = {
        "llm_provider-x-account-pool-request-id": str(request_id),
        "llm_provider-x-account-pool-account-id": str(fallback_id),
        "llm_provider-x-account-pool-attempt": "3",
    }
    assert completed_pool_metadata(metadata, headers) == {
        **metadata,
        "account_pool_account_id": str(fallback_id),
        "account_pool_attempt_count": 3,
    }
    assert (
        completed_pool_metadata(metadata, {**headers, "llm_provider-x-account-pool-request-id": str(uuid4())})
        == metadata
    )
    assert completed_pool_metadata({}, headers) == {}


def test_internal_forward_enforces_ticket_and_correlates_without_spend_sync(signing_secret):
    _, original = setup_gateway(lambda _: httpx.Response(200))
    control = InternalControl(original.resolution)
    app = FastAPI()
    app.add_middleware(
        AccountPoolGatewayMiddleware,
        control_factory=lambda _: control,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": "ok"}}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
                    },
                )
            )
        ),
    )
    identity = PoolIdentity(key_hash="test-key-hash", request_id=uuid4(), card_id=control.resolution.card_id)
    path = INTERNAL_PREFIX + str(identity.card_id) + "/v1/chat/completions"
    payload = {"model": "model-a", "messages": [{"role": "user", "content": "hello"}]}
    with TestClient(app) as client:
        assert client.post(path, json=payload).status_code == 401
        result = client.post(
            path, json=payload, headers={"Authorization": "Bearer " + create_ticket(identity, identity.card_id)}
        )
    assert result.status_code == 200
    assert result.headers["x-account-pool-request-id"] == str(identity.request_id)
    assert len(control.acquisitions) == 1
    assert control.acquisitions[0].request_id == identity.request_id
    assert len(control.finished) == 1
    assert control.finished[0].input_tokens == 5
    assert control.finished[0].spend_sync_state == "standard"


def test_router_filters_shared_model_and_rejects_explicit_foreign_deployment(signing_secret):
    card, other = uuid4(), uuid4()
    router = Router(
        model_list=[
            {
                "model_name": "shared",
                "litellm_params": {"model": "openai/gpt-5", "api_key": "test"},
                "model_info": {
                    "id": str(identifier),
                    "account_pool_environment_id": str(identifier),
                    "managed_by": "account_pool",
                },
            }
            for identifier in (card, other)
        ]
    )
    token = pool_identity.set(PoolIdentity(key_hash="test-hash", request_id=uuid4(), card_id=card))
    try:
        _, candidates = router._common_checks_available_deployment("shared")
        assert [item["model_info"]["id"] for item in candidates] == [str(card)]
        with pytest.raises(HTTPException):
            router._common_checks_available_deployment(str(other))
        kwargs = {}
        router._update_kwargs_with_deployment(candidates[0], kwargs)
        assert kwargs["api_base"].endswith(str(card) + "/v1")
        assert verify_ticket(kwargs["api_key"], card).identity.card_id == card
        assert kwargs["caching"] is False
    finally:
        pool_identity.reset(token)
        router.discard()


def test_public_card_key_reaches_standard_application():
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def ordinary():
        return {"standard_pipeline": True}

    app.add_middleware(AccountPoolGatewayMiddleware)
    with TestClient(app) as client:
        result = client.post("/v1/chat/completions", headers={"Authorization": "Bearer cpk_test"})
    assert result.json() == {"standard_pipeline": True}


def test_internal_websocket_validates_identity_and_releases_the_lease(signing_secret):
    from litellm.proxy.management_endpoints.account_pool_management_models import TransportPolicy

    _, original = setup_gateway(lambda _: httpx.Response(200))
    enabled = original.resolution.policy.model_copy(update={"transport": TransportPolicy(websocket="enabled")})
    card = original.resolution.candidates[0].model_copy(update={"policy": enabled, "websocket_enabled": True})
    control = InternalControl(
        original.resolution.model_copy(update={"policy": enabled, "candidates": (card,), "websocket_enabled": True})
    )
    dialer = FakeWebSocketDialer()
    identity = PoolIdentity(key_hash="virtual-key", request_id=uuid4(), card_id=card.id)
    app = FastAPI()
    app.add_middleware(AccountPoolGatewayMiddleware, control_factory=lambda _: control, websocket_dialer=dialer)
    with TestClient(app) as client:
        with client.websocket_connect(
            INTERNAL_PREFIX + str(card.id) + "/v1/responses?model=model-a",
            headers={"Authorization": "Bearer " + create_ticket(identity, card.id)},
        ) as websocket:
            websocket.send_json({"type": "response.create", "model": "model-a", "input": "hello"})
            assert websocket.receive_json()["type"] == "response.completed"
    assert len(control.acquisitions) == len(control.finished) == 1
    assert control.acquisitions[0].request_id == identity.request_id
    assert control.finished[0].spend_sync_state == "standard"


@pytest.mark.asyncio
async def test_scoped_endpoint_is_rejected_before_budget_reservation():
    from litellm.proxy._types import UserAPIKeyAuth
    from litellm.proxy.auth.user_api_key_auth import _authorize_authenticated_request

    request = Request({"type": "http", "path": "/key/generate", "headers": []})
    auth = UserAPIKeyAuth(api_key="sk-test", metadata={"account_pool_card_id": str(uuid4())})
    with patch("litellm.proxy.auth.user_api_key_auth._run_centralized_common_checks", new_callable=AsyncMock) as checks:
        with pytest.raises(HTTPException, match="endpoint"):
            await _authorize_authenticated_request(auth, request, {}, "/key/generate", "sk-test")
    checks.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("key,deleted,exists", [("cpk_legacy", True, True), ("sk-cpk_missing", False, False)])
async def test_deleted_or_unregistered_card_keys_cannot_register_again(key, deleted, exists):
    scope = CardScope(account_pool_card_id=uuid4(), account_pool_binding_id=uuid4())
    repository = SimpleNamespace(find_by_id=AsyncMock(return_value=object() if exists else None))
    tombstones = SimpleNamespace(
        table=SimpleNamespace(find_first=AsyncMock(return_value=object() if deleted else None))
    )
    with (
        patch("litellm.proxy.proxy_server.prisma_client", object()),
        patch(
            "litellm.proxy.management_endpoints.account_pool_integration.resolve_card_key",
            AsyncMock(return_value=scope),
        ),
        patch(
            "litellm.proxy.management_endpoints.account_pool_integration.VerificationTokenRepository",
            return_value=repository,
        ),
        patch(
            "litellm.proxy.management_endpoints.account_pool_integration.DeletedVerificationTokenRepository",
            return_value=tombstones,
        ),
        patch(
            "litellm.proxy.management_endpoints.key_management_endpoints.generate_key_helper_fn", new_callable=AsyncMock
        ) as create,
    ):
        with pytest.raises(HTTPException) as error:
            await register_card_key(key)
    assert error.value.status_code == 401
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_key_registration_preserves_plaintext_but_stores_named_card_scope(signing_secret):
    scope = CardScope(account_pool_card_id=uuid4(), account_pool_binding_id=uuid4())
    repository = SimpleNamespace(find_by_id=AsyncMock(return_value=None))
    tombstones = SimpleNamespace(table=SimpleNamespace(find_first=AsyncMock(return_value=None)))
    with (
        patch("litellm.proxy.proxy_server.prisma_client", object()),
        patch(
            "litellm.proxy.management_endpoints.account_pool_integration.resolve_card_key",
            AsyncMock(return_value=scope),
        ),
        patch(
            "litellm.proxy.management_endpoints.account_pool_integration.VerificationTokenRepository",
            return_value=repository,
        ),
        patch(
            "litellm.proxy.management_endpoints.account_pool_integration.DeletedVerificationTokenRepository",
            return_value=tombstones,
        ),
        patch(
            "litellm.proxy.management_endpoints.account_pool_gateway.http_client",
            lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"name": "测试卡片"}))
            ),
        ),
        patch(
            "litellm.proxy.management_endpoints.key_management_endpoints.generate_key_helper_fn", new_callable=AsyncMock
        ) as create,
    ):
        await register_card_key("cpk_legacy")
    assert create.await_args.kwargs["token"] == "cpk_legacy"
    assert create.await_args.kwargs["key_alias"] == "号池 测试卡片"
    assert create.await_args.kwargs["metadata"]["account_pool_card_id"] == str(scope.card_id)


@pytest.mark.asyncio
async def test_revocation_blocks_matching_virtual_key_and_invalidates_auth_cache():
    from litellm.proxy.management_endpoints.account_pool_integration import block_card_keys

    binding = uuid4()
    repository = SimpleNamespace(
        find_by_account_pool_binding=AsyncMock(return_value=[SimpleNamespace(token="key-hash")]), update=AsyncMock()
    )
    invalidated = []
    cache = SimpleNamespace(delete_cache=invalidated.append)
    with (
        patch("litellm.proxy.proxy_server.prisma_client", object()),
        patch("litellm.proxy.proxy_server.user_api_key_cache", cache),
        patch(
            "litellm.proxy.management_endpoints.account_pool_integration.VerificationTokenRepository",
            return_value=repository,
        ),
    ):
        await block_card_keys(binding)
    repository.find_by_account_pool_binding.assert_awaited_once_with(str(binding))
    repository.update.assert_awaited_once_with("key-hash", {"blocked": True}, id_field="token")
    assert invalidated == ["key-hash"]


def test_pool_correlation_survives_standard_spend_metadata_filtering():
    from litellm.litellm_core_utils.litellm_logging import get_standard_logging_metadata
    from litellm.proxy._types import UserAPIKeyAuth
    from litellm.proxy.litellm_pre_call_utils import LiteLLMProxyRequestSetup
    from litellm.proxy.spend_tracking.spend_tracking_utils import _get_spend_logs_metadata

    identity = PoolIdentity(key_hash="audit-key", request_id=uuid4(), card_id=uuid4())
    reset = pool_identity.set(identity)
    try:
        data = LiteLLMProxyRequestSetup.add_user_api_key_auth_to_request_metadata(
            data={"metadata": {"spend_logs_metadata": {"project": "audit"}}},
            user_api_key_dict=UserAPIKeyAuth(api_key="audit-key"),
            _metadata_variable_name="metadata",
        )
    finally:
        pool_identity.reset(reset)
    metadata = get_standard_logging_metadata(data["metadata"])
    stored = _get_spend_logs_metadata(metadata)
    assert stored["spend_logs_metadata"] == {
        "project": "audit",
        "account_pool_request_id": str(identity.request_id),
        "account_pool_card_id": str(identity.card_id),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protocol",
    [
        "chat",
        "responses",
        "compact",
        "stream",
        "stream_usage",
        "stream_finish_usage",
        "error",
        "stream_refusal",
        "stream_tool_refusal",
        "stream_late_refusal",
        "responses_stream",
        "responses_stream_error",
        "responses_stream_refusal",
        "responses_stream_untyped_error",
        "responses_stream_truncated",
    ],
)
async def test_real_router_http_request_enters_pool_before_upstream(signing_secret, monkeypatch, protocol):
    _, original = setup_gateway(lambda _: httpx.Response(200))
    control = InternalControl(original.resolution)
    from litellm.proxy.management_endpoints.account_pool_management_models import CodexPolicy

    control.resolution = control.resolution.model_copy(
        update={
            "policy": control.resolution.policy.model_copy(
                update={"codex": CodexPolicy(responses_compact_enabled=True)}
            )
        }
    )
    response_body = (
        {
            "id": "resp_pool",
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "model": "model-a",
            "output": [],
            "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
        }
        if protocol.startswith("responses") or protocol == "compact"
        else {
            "id": "chatcmpl-pool",
            "object": "chat.completion",
            "created": 1,
            "model": "model-a",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "through pool"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        }
    )
    if protocol == "compact":
        response_body["object"] = "response.compaction"

    def upstream(request):
        expected_path = (
            "/v1/responses/compact"
            if protocol == "compact"
            else "/v1/responses"
            if protocol.startswith("responses")
            else "/v1/chat/completions"
        )
        assert request.url.path == expected_path
        if protocol.startswith("responses_stream"):
            import json

            terminal = (
                {"type": "response.completed", "sequence_number": 2, "response": response_body}
                if protocol == "responses_stream"
                else {
                    **({"type": "error", "sequence_number": 2} if protocol != "responses_stream_untyped_error" else {}),
                    "error": {
                        "type": "invalid_request_error"
                        if protocol == "responses_stream_refusal"
                        else "service_unavailable_error",
                        "code": "invalid_prompt" if protocol == "responses_stream_refusal" else "server_is_overloaded",
                        "message": "private upstream error details",
                    },
                }
            )
            events = (
                {
                    "type": "response.created",
                    "sequence_number": 0,
                    "response": {**response_body, "status": "in_progress"},
                },
                {
                    "type": "response.output_text.delta",
                    "sequence_number": 1,
                    "item_id": "msg_pool",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "partial",
                },
                terminal,
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content="".join(
                    f"data: {json.dumps(event)}\n\n"
                    for event in (events[:-1] if protocol == "responses_stream_truncated" else events)
                ),
            )
        if protocol == "error":
            return httpx.Response(503, json={"error": {"message": "upstream unavailable", "type": "server_error"}})
        if protocol in ("stream_refusal", "stream_tool_refusal", "stream_late_refusal"):
            import json

            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content="data: "
                + json.dumps(
                    {
                        "id": "chatcmpl-pool",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "model-a",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "role": "assistant",
                                    "content": "partial" if protocol == "stream_late_refusal" else "",
                                },
                            }
                        ],
                    }
                )
                + '\n\ndata: {"error":{"type":"invalid_request_error","code":"invalid_prompt"}}\n\n',
            )
        if protocol in ("stream", "stream_usage", "stream_finish_usage"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    'data: {"id":"chatcmpl-pool","object":"chat.completion.chunk","created":1,"model":"model-a","choices":[{"index":0,"delta":{"role":"assistant","content":"through pool"},"finish_reason":null}]}\n\n'
                    + (
                        'data: {"id":"chatcmpl-pool","object":"chat.completion.chunk","created":1,"model":"model-a","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":2877,"completion_tokens":5,"total_tokens":2882,"prompt_tokens_details":{"cached_tokens":2048}}}\n\n'
                        if protocol == "stream_finish_usage"
                        else 'data: {"id":"chatcmpl-pool","object":"chat.completion.chunk","created":1,"model":"model-a","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
                        'data: {"id":"chatcmpl-pool","object":"chat.completion.chunk","created":1,"model":"model-a","choices":[],"usage":{"prompt_tokens":2877,"completion_tokens":5,"total_tokens":2882,"prompt_tokens_details":{"cached_tokens":2048}}}\n\n'
                    )
                    + "data: [DONE]\n\n"
                ),
            )
        return httpx.Response(200, json=response_body)

    app = FastAPI()
    app.add_middleware(
        AccountPoolGatewayMiddleware,
        control_factory=lambda _: control,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    monkeypatch.setenv("ACCOUNT_POOL_INTERNAL_URL", f"http://127.0.0.1:{port}")
    card = control.resolution.card_id
    router = Router(
        model_list=[
            {
                "model_name": "model-a",
                "litellm_params": {
                    "model": "openai/model-a",
                    "api_key": "placeholder",
                    "api_base": f"http://127.0.0.1:{port}/invalid",
                    "max_parallel_requests": 4,
                    "num_retries": 0,
                    "input_cost_per_token": 0.0000002,
                    "output_cost_per_token": 0.0000012,
                    "cache_read_input_token_cost": 0.00000005,
                },
                "model_info": {"id": str(card), "account_pool_environment_id": str(card), "managed_by": "account_pool"},
            }
        ],
        num_retries=3,
        retry_after=0,
    )
    token = pool_identity.set(PoolIdentity(key_hash="standard-virtual-key", request_id=uuid4()))
    logged = asyncio.Queue()

    async def on_success(kwargs, response_obj, start_time, end_time):
        await logged.put((kwargs, response_obj))

    failures = queue.Queue()

    def on_failure(kwargs, response_obj, start_time, end_time):
        failures.put(kwargs)

    try:
        assert server.started
        if protocol.startswith("responses_stream"):

            async def consume():
                stream = await router.aresponses(
                    model="model-a",
                    input="hello",
                    stream=True,
                    success_callback=[on_success],
                    failure_callback=[on_failure],
                )
                return [chunk async for chunk in stream]

            if protocol == "responses_stream":
                chunks = await consume()
                assert chunks[-1].type == "response.completed"
                logging_kwargs, _ = await asyncio.wait_for(logged.get(), timeout=5)
                assert logging_kwargs["standard_logging_object"]["status"] == "success"
            else:
                with pytest.raises(Exception) as failure:
                    await consume()
                assert getattr(failure.value, "status_code", None) == (
                    400
                    if protocol == "responses_stream_refusal"
                    else 502
                    if protocol == "responses_stream_truncated"
                    else 503
                )
                assert "private upstream error details" not in str(failure.value)
                logging_kwargs = await asyncio.to_thread(failures.get, timeout=5)
                assert logging_kwargs["standard_logging_object"]["status"] == "failure"
        elif protocol == "responses":
            response = await router.aresponses(model="model-a", input="hello")
            assert response.usage.total_tokens == 7
        elif protocol == "compact":
            response = await router.acompact_responses(model="model-a", input=[{"role": "user", "content": "hello"}])
            assert response.object == "response.compaction"
        elif protocol == "error":
            with pytest.raises(Exception, match="upstream unavailable"):
                await router.acompletion(model="model-a", messages=[{"role": "user", "content": "hello"}])
        elif protocol in ("stream_refusal", "stream_tool_refusal", "stream_late_refusal"):
            from litellm import BadRequestError

            with pytest.raises(BadRequestError, match="invalid_prompt") as failure:
                response = await router.acompletion(
                    model="model-a",
                    messages=[{"role": "user", "content": "hello"}],
                    stream=True,
                    **(
                        {
                            "tools": [
                                {"type": "function", "function": {"name": "test", "parameters": {"type": "object"}}}
                            ]
                        }
                        if protocol == "stream_tool_refusal"
                        else {}
                    ),
                )
                async for chunk in response:
                    pass
            assert failure.value.status_code == 400
        elif protocol in ("stream", "stream_usage", "stream_finish_usage"):
            response = await router.acompletion(
                model="model-a",
                stream=True,
                messages=[{"role": "user", "content": "hello"}],
                success_callback=[on_success],
                **({"stream_options": {"include_usage": True}} if protocol != "stream" else {}),
            )
            chunks = [chunk async for chunk in response]
            assert any(chunk.choices and chunk.choices[0].delta.content == "through pool" for chunk in chunks)
            from litellm import stream_chunk_builder

            assembled = stream_chunk_builder(response.chunks)
            assert assembled.usage.prompt_tokens == 2877
            assert assembled.usage.completion_tokens == 5
            assert assembled.usage.prompt_tokens_details.cached_tokens == 2048
            if protocol != "stream":
                assert chunks[-1].usage == assembled.usage
            logging_kwargs, logged_response = await asyncio.wait_for(logged.get(), timeout=5)
            assert logged_response.usage == assembled.usage
            assert logging_kwargs["response_cost"] == pytest.approx(0.0002742)
            standard_log = logging_kwargs["standard_logging_object"]
            assert standard_log["prompt_tokens"] == 2877
            assert standard_log["completion_tokens"] == 5
            assert standard_log["response_cost"] == pytest.approx(0.0002742)
            assert standard_log["metadata"]["spend_logs_metadata"]["account_pool_account_id"] == str(card)
            assert standard_log["metadata"]["spend_logs_metadata"]["account_pool_request_id"] == str(
                pool_identity.get().request_id
            )
            assert standard_log["hidden_params"]["additional_headers"]["llm_provider-x-account-pool-account-id"] == str(
                card
            )
        else:
            response = await router.acompletion(model="model-a", messages=[{"role": "user", "content": "hello"}])
            assert response.choices[0].message.content == "through pool"
            assert response.usage.total_tokens == 7
        expected_attempts = 2 if protocol == "error" else 1
        assert len(control.acquisitions) == expected_attempts
        finish_deadline = time.monotonic() + 5
        while len(control.finished) < expected_attempts and time.monotonic() < finish_deadline:
            await asyncio.sleep(0.01)
        assert len(control.finished) == expected_attempts
        assert control.finished[0].spend_sync_state == "standard"
        if protocol in ("stream", "stream_usage", "stream_finish_usage"):
            assert control.finished[0].input_tokens == assembled.usage.prompt_tokens
            assert control.finished[0].output_tokens == assembled.usage.completion_tokens
            assert control.finished[0].cache_read_input_tokens == assembled.usage.prompt_tokens_details.cached_tokens
    finally:
        pool_identity.reset(token)
        router.discard()
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        from litellm import close_litellm_async_clients

        await close_litellm_async_clients()
