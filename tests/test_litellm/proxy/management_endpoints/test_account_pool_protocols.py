"""验证协议策略绑定已鉴权身份和所选卡片，不影响普通部署。"""
from uuid import uuid4

import pytest

import litellm
from litellm.main import responses_api_bridge_check
from litellm.proxy.management_endpoints.account_pool_integration import PoolIdentity, forwarding_base, pool_identity
from litellm.proxy.management_endpoints.account_pool_native_routing import (
    RoutingSnapshot,
    eligible_deployments,
    now_utc,
    snapshots,
)
from litellm.proxy.management_endpoints.account_pool_protocols import passthrough_for_base, request_protocol


@pytest.mark.parametrize("path, expected", [("/responses", "responses"), ("/v1/responses/compact", "responses"), ("/v1/chat/completions", "chat"), ("/v1/messages", "messages"), ("/v1/images/generations", "images"), ("/models", None)])
def test_protocol_paths(path, expected):
    assert request_protocol(path) == expected

def test_passthrough_disables_chat_bridge_only_for_authenticated_selected_card(monkeypatch):
    account = uuid4()
    monkeypatch.setattr(snapshots, "values", {str(account): RoutingSnapshot(passthrough_enabled=True)})
    monkeypatch.setattr(litellm, "route_all_chat_openai_to_responses", True)
    token = pool_identity.set(PoolIdentity(key_hash="test", request_id=uuid4(), protocol="chat"))
    try:
        assert passthrough_for_base(forwarding_base(account))
        info, model = responses_api_bridge_check("gpt-5.4", "openai", api_base=forwarding_base(account))
        assert info.get("mode") != "responses" and model == "gpt-5.4"
        assert responses_api_bridge_check("gpt-5.4", "openai", api_base="https://example.test/v1")[0]["mode"] == "responses"
    finally:
        pool_identity.reset(token)
    assert not passthrough_for_base(forwarding_base(account))

def test_virtual_key_prefilter_skips_cards_without_ingress_protocol():
    a, b = str(uuid4()), str(uuid4())
    deployments = [{"model_info": {"account_pool_environment_id": account}} for account in (a,b)]
    token = pool_identity.set(PoolIdentity(key_hash="test", request_id=uuid4(), protocol="responses"))
    try:
        values = {a: RoutingSnapshot(supported_protocols=("chat",)), b: RoutingSnapshot(supported_protocols=("responses",))}
        assert eligible_deployments(deployments, "shared", values, now_utc()) == [deployments[1]]
    finally:
        pool_identity.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "responses"])
async def test_real_router_and_sdk_keep_protocol_when_passthrough_enabled(monkeypatch, mode):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from litellm import Router

    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"]))), self.headers["Authorization"]))
            data = ({"id": "chat_test", "object": "chat.completion", "created": 1, "model": "gpt-5.6", "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}
                    if mode == "chat" else {"id": "resp_test", "object": "response", "created_at": 1, "model": "gpt-5.6", "status": "completed", "output": [{"type": "message", "id": "msg_test", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "Hello", "annotations": []}]}], "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}})
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    account = uuid4()
    monkeypatch.setenv("ACCOUNT_POOL_MANAGER_TOKEN", "x" * 32)
    monkeypatch.setenv("ACCOUNT_POOL_INTERNAL_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(litellm, "route_all_chat_openai_to_responses", True)
    monkeypatch.setattr(snapshots, "values", {str(account): RoutingSnapshot(passthrough_enabled=True)})
    router = Router(model_list=[{"model_name": "shared", "litellm_params": {"model": "openai/gpt-5.6", "api_key": "unused", "use_chat_completions_api": True}, "model_info": {"id": "card", "account_pool_environment_id": str(account)}}], num_retries=0)
    token = pool_identity.set(PoolIdentity(key_hash="virtual", request_id=uuid4(), protocol=mode))
    try:
        if mode == "chat":
            result = await router.acompletion(model="shared", messages=[{"role": "user", "content": "hi"}], disable_fallbacks=True)
            assert result.choices[0].message.content == "Hello"
        else:
            result = await router.aresponses(model="shared", input="hi", disable_fallbacks=True)
            assert result.status == "completed"
        assert len(calls) == 1
        assert calls[0][0].endswith("/chat/completions" if mode == "chat" else "/responses")
        assert calls[0][1]["model"] == "gpt-5.6"
        assert calls[0][2].startswith("Bearer ") and "unused" not in calls[0][2]
    finally:
        pool_identity.reset(token)
        router.discard()
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("path", ["/v1/realtime", "/v1/responses", "/v1/messages", "/v1/images/generations"])
def test_empty_protocol_selection_rejects_http_and_websocket_paths(path):
    from starlette.datastructures import Headers

    from litellm.proxy.management_endpoints.account_pool_management_models import AccountPolicy, TransportPolicy
    from litellm.proxy.management_endpoints.account_pool_routing import protocol_policy
    rejected = protocol_policy(AccountPolicy(transport=TransportPolicy(supported_protocols=())), path, Headers(), False)
    assert rejected is not None and rejected.code == "account_pool_protocol_disabled"


@pytest.mark.asyncio
async def test_websocket_checks_each_turn_and_moves_before_output():
    import asyncio
    import json

    from starlette.datastructures import Headers
    from starlette.websockets import WebSocket

    from litellm.proxy.management_endpoints.account_pool_gateway_contracts import AcquireRejected
    from litellm.proxy.management_endpoints.account_pool_management_models import TransportPolicy
    from litellm.proxy.management_endpoints.account_pool_routing import routes
    from litellm.proxy.management_endpoints.account_pool_websocket import forward_websocket
    from tests.test_litellm.proxy.management_endpoints.test_account_pool_gateway import (
        FakeWebSocketDialer,
        setup_gateway,
    )

    dialer = FakeWebSocketDialer()
    _, control = setup_gateway(lambda _: None, retry=True, websocket_dialer=dialer)
    policy = control.resolution.policy.model_copy(update={"transport": TransportPolicy(websocket="enabled")})
    accounts = tuple(account.model_copy(update={"policy": policy, "websocket_enabled": True}) for account in control.resolution.candidates)
    control.resolution = control.resolution.model_copy(update={"policy": policy, "candidates": accounts, "websocket_enabled": True})
    first = accounts[0].id
    acquire = control.acquire
    async def acquire_turn(request):
        if len(control.finished) and request.account_id == first:
            control.acquisitions.append(request)
            return AcquireRejected(reason="model_cooldown")
        return await acquire(request)
    control.acquire = acquire_turn
    inbound = asyncio.Queue()
    outbound = asyncio.Queue()
    await inbound.put({"type": "websocket.connect"})
    socket = WebSocket({"type": "websocket", "path": "/v1/responses", "query_string": b"model=model-a", "headers": [], "scheme": "ws"}, inbound.get, outbound.put)
    selected = routes(control.resolution, "model-a", "/v1/responses", Headers(), websocket=True)
    task = asyncio.create_task(forward_websocket(socket, "test", "model-a", None, control.resolution, selected, control, dialer))
    assert (await asyncio.wait_for(outbound.get(), 3))["type"] == "websocket.accept"
    await inbound.put({"type": "websocket.receive", "text": json.dumps({"type": "response.create", "model": "model-a", "input": "one"})})
    await asyncio.wait_for(outbound.get(), 3)
    await inbound.put({"type": "websocket.receive", "text": json.dumps({"type": "response.create", "model": "model-a", "input": "two"})})
    await asyncio.wait_for(outbound.get(), 3)
    await inbound.put({"type": "websocket.disconnect", "code": 1000})
    await asyncio.wait_for(task, 2)
    assert [item.account_id for item in control.acquisitions] == [first, first, accounts[1].id]
    assert len(control.finished) == 2
    assert len(dialer.requests) == 2

@pytest.mark.asyncio
@pytest.mark.parametrize('scenario', ['quota', 'quota_lifecycle', 'quota_no_b', 'visible', 'history', 'unknown', 'bound', 'paired_tools', 'web_search', 'signed_image', 'oversize', 'file_ref', 'instructions', 'context_changed', 'invalid_input', 'cancel'])
async def test_websocket_turn_recovery_boundaries(scenario):
    import asyncio
    import json
    from contextlib import asynccontextmanager

    from starlette.datastructures import Headers
    from starlette.websockets import WebSocket

    from litellm.proxy.management_endpoints.account_pool_gateway_contracts import AcquireRejected
    from litellm.proxy.management_endpoints.account_pool_management_models import TransportPolicy
    from litellm.proxy.management_endpoints.account_pool_routing import routes
    from litellm.proxy.management_endpoints.account_pool_websocket import forward_websocket
    from tests.test_litellm.proxy.management_endpoints.test_account_pool_gateway import Control, setup_gateway

    _, original = setup_gateway(lambda _: None, retry=True)
    policy = original.resolution.policy.model_copy(update={'transport': TransportPolicy(websocket='enabled')})
    accounts = tuple(account.model_copy(update={'policy': policy, 'websocket_enabled': True}) for account in original.resolution.candidates)
    class TurnControl(Control):
        async def acquire(self, request):
            if self.finished and scenario == 'quota_no_b' and request.account_id == accounts[1].id:
                self.acquisitions.append(request)
                return AcquireRejected(reason='model_cooldown', retry_after_seconds=30)
            if self.finished and scenario in ('history', 'unknown', 'paired_tools', 'web_search', 'signed_image', 'oversize', 'file_ref', 'instructions', 'context_changed') and request.account_id == accounts[0].id:
                self.acquisitions.append(request)
                return AcquireRejected(reason='model_cooldown')
            return await super().acquire(request)
    control = TurnControl(original.resolution.model_copy(update={'policy': policy, 'candidates': accounts[:1] if scenario == 'bound' else accounts, 'websocket_enabled': True}))
    class Upstream:
        subprotocol = None
        def __init__(self, index):
            self.index = index
            self.events = asyncio.Queue()
            self.sent = []
            self.closed = False
            self.create_received = asyncio.Event()
            self.cancel_forwarded = asyncio.Event()
        async def send(self, message):
            payload = json.loads(message)
            self.sent.append(payload)
            if scenario == 'cancel':
                if payload.get('type') == 'response.create':
                    self.create_received.set()
                elif payload.get('type') == 'response.cancel':
                    self.cancel_forwarded.set()
                    await self.events.put(json.dumps({'type': 'response.incomplete', 'response': {'id': 'resp_cancelled'}}))
                return
            if self.index == 0 and scenario in ('quota', 'quota_lifecycle', 'quota_no_b', 'visible', 'bound'):
                if scenario == 'visible':
                    await self.events.put(json.dumps({'type': 'response.output_text.delta', 'delta': 'visible'}))
                if scenario == 'quota_lifecycle':
                    await self.events.put(json.dumps({'type': 'response.created', 'response': {'id': 'discarded'}}))
                    await self.events.put(json.dumps({'type': 'response.in_progress'}))
                await self.events.put(json.dumps({'type': 'error', 'error': {'code': 'usage_limit_reached', 'message': 'exhausted'}}))
            else:
                output = ([{'type': 'reasoning', 'encrypted_content': 'foreign-secret', 'summary': [{'type': 'summary_text', 'text': 'visible reason'}]}, {'type': 'image_generation_call', 'status': 'completed', 'result': 'iVBORw0KGgo='}] if scenario == 'signed_image' else
                          [{'type': 'message', 'role': 'assistant', 'content': 'x' * (256 * 1024)}] if scenario == 'oversize' else
                          [{'type': 'future_output', 'payload': 'opaque'}] if scenario == 'unknown' else
                          [{'type': 'custom_tool_call', 'call_id': 'c1', 'name': 'custom', 'input': 'raw'}] if scenario == 'paired_tools' else
                          [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'reply'}]}])
                await self.events.put(json.dumps({'type': 'response.completed', 'response': {'id': f'resp_{self.index}', 'model': payload.get('model'), 'output': output}}))
        async def recv(self):
            return await self.events.get()
        async def close(self, code=1000, reason=''):
            self.closed = True
    class Dialer:
        def __init__(self):
            self.upstreams = []
        @asynccontextmanager
        async def connect(self, request):
            upstream = Upstream(len(self.upstreams))
            self.upstreams.append(upstream)
            try:
                yield upstream
            finally:
                await upstream.close()
    dialer = Dialer()
    inbound, outbound = asyncio.Queue(), asyncio.Queue()
    await inbound.put({'type': 'websocket.connect'})
    socket = WebSocket({'type': 'websocket', 'path': '/v1/responses', 'query_string': b'model=model-a', 'headers': [], 'scheme': 'ws'}, inbound.get, outbound.put)
    selected = routes(control.resolution, 'model-a', '/v1/responses', Headers(), websocket=True)
    task = asyncio.create_task(forward_websocket(socket, 'test', 'model-a', None, control.resolution, selected, control, dialer))
    await asyncio.wait_for(outbound.get(), 3)
    async def send(payload):
        await inbound.put({'type': 'websocket.receive', 'text': json.dumps({'type': 'response.create', 'model': 'model-a', **payload})})
    async def receive():
        frame = await asyncio.wait_for(outbound.get(), 3)
        assert frame['type'] == 'websocket.send'
        return json.loads(frame['text'])
    try:
        first_params = {'input': [{'role': 'user', 'content': 'one'}, 123]} if scenario == 'invalid_input' else {'input': 'one'}
        if scenario in ('instructions', 'context_changed'):
            first_params['instructions'] = 'old instructions'
        if scenario == 'web_search':
            first_params['tools'] = [{'type': 'web_search'}]
        if scenario == 'paired_tools':
            first_params['tools'] = [{'type': 'custom', 'name': 'custom', 'format': {'type': 'text'}}]
        await send(first_params)
        if scenario == 'cancel':
            await dialer.upstreams[0].create_received.wait()
            await inbound.put({'type': 'websocket.receive', 'text': json.dumps({'type': 'response.cancel'})})
            await asyncio.wait_for(dialer.upstreams[0].cancel_forwarded.wait(), 0.1)
        first = await receive()
        if scenario in ('history', 'unknown', 'paired_tools', 'web_search', 'signed_image', 'oversize', 'file_ref', 'instructions', 'context_changed'):
            delta = ([{'type': 'custom_tool_call_output', 'call_id': 'c1', 'output': 'result'}, {'role': 'user', 'content': 'two'}]
                     if scenario == 'paired_tools' else [{'role': 'user', 'content': 'two'}])
            if scenario == 'file_ref':
                delta = [{'role': 'user', 'content': [{'type': 'input_file', 'file_id': 'private-file'}]}]
            await send({'previous_response_id': first['response']['id'], 'input': delta, **({'instructions': 'new instructions'} if scenario == 'context_changed' else {})})
            second = await receive()
            if scenario in ('unknown', 'oversize', 'file_ref'):
                assert second['error']['code'] == 'account_pool_history_unavailable'
                assert len(dialer.upstreams) == 1
            else:
                assert second['type'] == 'response.completed'
                replay = dialer.upstreams[1].sent[0]
                assert 'previous_response_id' not in replay
                assert replay['input'][0] == {'role': 'user', 'content': 'one'}
                assert replay['input'][-1] == {'role': 'user', 'content': 'two'}
                assert len(replay['input']) == (4 if scenario in ('paired_tools', 'signed_image') else 3)
                if scenario == 'signed_image':
                    assert 'foreign-secret' not in json.dumps(replay)
                    assert replay['input'][1]['summary'][0]['text'] == 'visible reason'
                    assert replay['input'][2]['content'][1]['image_url'] == 'data:image/png;base64,iVBORw0KGgo='
                if scenario == 'web_search':
                    assert replay['tools'] == [{'type': 'web_search'}]
                if scenario == 'paired_tools':
                    assert replay['tools'][0]['name'] == 'custom'
                if scenario in ('instructions', 'context_changed'):
                    assert replay['instructions'] == ('new instructions' if scenario == 'context_changed' else 'old instructions')
        elif scenario == 'invalid_input':
            assert first['error']['code'] == 'invalid_request_error'
            assert dialer.upstreams[0].sent == []
        elif scenario == 'visible':
            assert first['delta'] == 'visible'
            assert (await receive())['error']['code'] == 'usage_limit_reached'
            assert len(dialer.upstreams) == 1
        elif scenario == 'bound':
            assert first['error']['code'] == 'usage_limit_reached'
            assert len(dialer.upstreams) == 1
        elif scenario == 'quota_no_b':
            assert first['error']['code'] == 'account_pool_no_eligible_accounts'
            assert first['error']['reasons'] == ['quota_exhausted', 'model_cooldown']
            assert first['error']['retry_after_seconds'] == 30
            assert len(dialer.upstreams) == 1
        elif scenario == 'cancel':
            assert first['type'] == 'response.incomplete'
            assert control.finished[-1].http_status == 499
        else:
            assert first['type'] == 'response.completed'
            assert len(dialer.upstreams) == 2
            assert control.finished[0].failure_reason == 'quota_exhausted'
    finally:
        await inbound.put({'type': 'websocket.disconnect', 'code': 1000})
        await asyncio.wait_for(task, 3)
    assert all(upstream.closed for upstream in dialer.upstreams)
    assert len(control.finished) == (2 if len(dialer.upstreams) == 2 else 1)


@pytest.mark.asyncio
async def test_websocket_continues_to_third_account_when_second_dial_fails():
    import asyncio
    import json
    from contextlib import asynccontextmanager

    from starlette.datastructures import Headers
    from starlette.websockets import WebSocket

    from litellm.proxy.management_endpoints.account_pool_management_models import TransportPolicy
    from litellm.proxy.management_endpoints.account_pool_routing import routes
    from litellm.proxy.management_endpoints.account_pool_websocket import forward_websocket
    from tests.test_litellm.proxy.management_endpoints.test_account_pool_gateway import (
        Control,
        candidate,
        setup_gateway,
    )

    _, original = setup_gateway(lambda _: None, retry=True, max_attempts=3)
    policy = original.resolution.policy.model_copy(update={'transport': TransportPolicy(websocket='enabled')})
    raw_accounts = (*original.resolution.candidates, candidate(uuid4()))
    accounts = tuple(account.model_copy(update={'policy': policy, 'websocket_enabled': True}) for account in raw_accounts)
    control = Control(original.resolution.model_copy(update={
        'policy': policy,
        'candidates': accounts,
        'websocket_enabled': True,
    }))

    class Upstream:
        subprotocol = None

        def __init__(self, index):
            self.index = index
            self.events = asyncio.Queue()
            self.sent = []
            self.closed = False

        async def send(self, message):
            payload = json.loads(message)
            self.sent.append(payload)
            if self.index == 0 and len(self.sent) == 2:
                event = {'type': 'error', 'error': {'code': 'usage_limit_reached'}}
            else:
                event = {
                    'type': 'response.completed',
                    'response': {
                        'id': 'resp_first' if self.index == 0 else 'resp_third',
                        'model': payload['model'],
                        'output': [{'type': 'message', 'role': 'assistant', 'content': 'reply'}],
                    },
                }
            await self.events.put(json.dumps(event))

        async def recv(self):
            return await self.events.get()

        async def close(self, code=1000, reason=''):
            self.closed = True

    class Dialer:
        def __init__(self):
            self.requests = []
            self.upstreams = []

        @asynccontextmanager
        async def connect(self, request):
            self.requests.append(request)
            index = len(self.requests) - 1
            if index == 1:
                raise OSError('second account unavailable')
            upstream = Upstream(index)
            self.upstreams.append(upstream)
            try:
                yield upstream
            finally:
                await upstream.close()

    dialer = Dialer()
    inbound, outbound = asyncio.Queue(), asyncio.Queue()
    await inbound.put({'type': 'websocket.connect'})
    socket = WebSocket(
        {'type': 'websocket', 'path': '/v1/responses', 'query_string': b'model=model-a', 'headers': [], 'scheme': 'ws'},
        inbound.get,
        outbound.put,
    )
    selected = routes(control.resolution, 'model-a', '/v1/responses', Headers(), websocket=True)
    assert tuple(route.account.id for route in selected) == tuple(account.id for account in accounts)
    task = asyncio.create_task(
        forward_websocket(socket, 'test', 'model-a', None, control.resolution, selected, control, dialer)
    )
    await asyncio.wait_for(outbound.get(), 3)

    async def send(payload):
        await inbound.put({'type': 'websocket.receive', 'text': json.dumps({'type': 'response.create', **payload})})

    async def receive():
        frame = await asyncio.wait_for(outbound.get(), 3)
        assert frame['type'] == 'websocket.send'
        return json.loads(frame['text'])

    try:
        await send({'model': 'model-a', 'input': 'one'})
        first = await receive()
        await send({'model': 'model-a', 'previous_response_id': first['response']['id'], 'input': 'two'})
        second = await receive()
        assert second['type'] == 'response.completed'
        assert len(dialer.requests) == 3
        assert [item.account_id for item in control.acquisitions] == [
            accounts[0].id,
            accounts[0].id,
            accounts[1].id,
            accounts[2].id,
        ]
        assert [item.http_status for item in control.finished] == [200, 429, 502, 200]
        assert dialer.upstreams[1].sent[0]['input'] == [
            {'role': 'user', 'content': 'one'},
            {'type': 'message', 'role': 'assistant', 'content': 'reply'},
            {'role': 'user', 'content': 'two'},
        ]
    finally:
        await inbound.put({'type': 'websocket.disconnect', 'code': 1000})
        await asyncio.wait_for(task, 3)
