"""验证协议策略绑定已鉴权身份和所选卡片，不影响普通部署。"""
from uuid import uuid4
import pytest
import litellm
from litellm.main import responses_api_bridge_check
from litellm.proxy.management_endpoints.account_pool_integration import PoolIdentity, pool_identity, forwarding_base
from litellm.proxy.management_endpoints.account_pool_native_routing import RoutingSnapshot, snapshots, eligible_deployments, now_utc
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
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
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
        pool_identity.reset(token); router.discard(); server.shutdown(); server.server_close(); thread.join()


@pytest.mark.parametrize("path", ["/v1/realtime", "/v1/responses", "/v1/messages", "/v1/images/generations"])
def test_empty_protocol_selection_rejects_http_and_websocket_paths(path):
    from starlette.datastructures import Headers
    from litellm.proxy.management_endpoints.account_pool_management_models import AccountPolicy, TransportPolicy
    from litellm.proxy.management_endpoints.account_pool_routing import protocol_policy
    rejected = protocol_policy(AccountPolicy(transport=TransportPolicy(supported_protocols=())), path, Headers(), False)
    assert rejected is not None and rejected.code == "account_pool_protocol_disabled"
