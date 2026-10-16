"""验证卡片连接测试的权限、固定路由与完整响应判定。"""
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_integration import pool_identity
from litellm.proxy.management_endpoints.account_pool_model_test import create_model_test_router

CARD = uuid4()

class RouterDouble:
    def __init__(self, result: object):
        self.result = result
        self.calls = []
    def get_model_list(self, model_name=None):
        return [
            {"model_name": "test-model", "model_info": {"id": "other", "account_pool_environment_id": str(uuid4())}},
            {"model_name": "test-model", "model_info": {"id": "selected", "account_pool_environment_id": str(CARD)}},
        ]
    async def acompletion(self, **kwargs):
        self.calls.append((kwargs, pool_identity.get()))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result
    aresponses = acompletion


def client_for(router, admin=True):
    async def manager(method, path, body):
        assert method == "GET" and path == f"/api/environments/{CARD}"
        return httpx.Response(200, json={"enabled": True, "enabled_models": ["test-model"]})
    def require_admin(user):
        if not admin:
            raise HTTPException(403)
    app = FastAPI()
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(api_key="hashed-admin")
    app.include_router(create_model_test_router(manager, require_admin, lambda: router), prefix="/account_pool")
    return TestClient(app)

@pytest.mark.parametrize("mode,result", [
    ("chat", {"choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}]}),
    ("responses", {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}]}),
])
def test_selected_card_only_and_no_retry(mode, result):
    router = RouterDouble(result)
    response = client_for(router).post(f"/account_pool/environments/{CARD}/test", json={"model": "test-model", "mode": mode, "stream": False})
    assert response.status_code == 200
    assert response.json()["success"] is True
    kwargs, identity = router.calls[0]
    assert kwargs["model"] == "selected" and kwargs["specific_deployment"] is True
    assert kwargs["num_retries"] == 0 and kwargs["disable_fallbacks"] is True
    assert identity.card_id == CARD and identity.router_settings.num_retries == 0
    assert identity.router_settings.retry_policy.InternalServerErrorRetries == 0
    assert len(router.calls) == 1
    assert pool_identity.get() is None

@pytest.mark.parametrize("mode,result", [
    ("chat", {"choices": []}),
    ("chat", {"choices": [{"message": {"content": "hi"}, "finish_reason": "length"}]}),
    ("responses", {"status": "incomplete", "output": []}),
    ("responses", {"status": "completed", "output": []}),
])
def test_http200_is_not_success_without_complete_text(mode, result):
    response = client_for(RouterDouble(result)).post(f"/account_pool/environments/{CARD}/test", json={"model": "test-model", "mode": mode})
    assert response.json()["success"] is False

@pytest.mark.parametrize("mode,events,success", [
    ("chat", [{"choices": [{"delta": {"content": "Hello"}, "finish_reason": None}]}, {"choices": [{"delta": {}, "finish_reason": "stop"}]}], True),
    ("chat", [{"choices": [{"delta": {"content": "Hello"}, "finish_reason": None}]}], False),
    ("responses", [{"type": "response.completed", "response": {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}]}}], True),
    ("responses", [{"type": "response.output_text.delta", "delta": "Hello"}, {"type": "response.failed"}], False),
])
def test_stream_must_complete(mode, events, success):
    async def stream() -> AsyncIterator[object]:
        for event in events:
            yield event
    response = client_for(RouterDouble(stream())).post(f"/account_pool/environments/{CARD}/test", json={"model": "test-model", "mode": mode, "stream": True})
    assert response.json()["success"] is success

@pytest.mark.parametrize("admin,model,status", [(False, "test-model", 403), (True, "missing", 400)])
def test_rejects_unauthorized_or_disabled_model(admin, model, status):
    router = RouterDouble({})
    response = client_for(router, admin).post(f"/account_pool/environments/{CARD}/test", json={"model": model})
    assert response.status_code == status and not router.calls

def test_error_does_not_expose_credentials():
    response = client_for(RouterDouble(RuntimeError("Bearer sk-secret https://private/?token=abc"))).post(f"/account_pool/environments/{CARD}/test", json={"model": "test-model"})
    assert response.json()["success"] is False
    assert "sk-secret" not in response.text and "private" not in response.text
