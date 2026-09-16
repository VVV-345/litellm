"""本文件验证项目版本接口仅限管理员，确认票据按身份转发且协议与部署后台一致。"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.common_utils.resource_ownership import is_proxy_admin
from litellm.proxy.management_endpoints import account_pool_release_models as contracts
from litellm.proxy.management_endpoints.account_pool_endpoints import (
    AccountPoolManagerClient,
    create_account_pool_router,
)
from litellm.proxy.management_endpoints.account_pool_releases import create_release_router


def test_public_and_worker_contracts_match() -> None:
    source: Final = Path(__file__).resolve().parents[4] / "account-pool/account_pool/release_models.py"
    assert ast.dump(ast.parse(source.read_text(encoding="utf-8"))) == ast.dump(
        ast.parse(Path(contracts.__file__).read_text(encoding="utf-8"))
    )


@pytest.mark.parametrize("admin", (True, False))
def test_admin_authentication_and_internal_token_forwarding(monkeypatch: pytest.MonkeyPatch, admin: bool) -> None:
    monkeypatch.setenv("ACCOUNT_POOL_RELEASE_TOKEN", "s" * 32)
    app: Final = FastAPI()

    def auth() -> UserAPIKeyAuth:
        return UserAPIKeyAuth(
            user_id="administrator", user_role=LitellmUserRoles.PROXY_ADMIN if admin else LitellmUserRoles.INTERNAL_USER
        )

    def require_admin(user: UserAPIKeyAuth) -> None:
        if not is_proxy_admin(user):
            raise HTTPException(403, "admin only")

    def transport(request: httpx.Request) -> httpx.Response:
        assert admin
        assert request.url.host == "release-worker"
        assert request.headers["Authorization"] == "Bearer " + "s" * 32
        assert len(request.headers["X-Release-Actor"]) == 64
        assert request.content == b'{"token":"' + b"a" * 64 + b'"}'
        return httpx.Response(409, json={"detail": "请等待确认倒计时结束"})

    app.dependency_overrides[user_api_key_auth] = auth
    app.include_router(
        create_release_router(require_admin, lambda: httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    )
    with TestClient(app) as client:
        response: Final = client.post("/releases/execute", json={"token": "a" * 64})
    assert response.status_code == (409 if admin else 403)
    assert "s" * 32 not in response.text
    if admin:
        assert response.json()["detail"] == "请等待确认倒计时结束"


def test_missing_worker_is_explicit_and_schema_generates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACCOUNT_POOL_RELEASE_TOKEN", raising=False)
    app: Final = FastAPI()
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(user_id="admin")
    app.include_router(create_release_router(lambda _: None))
    assert "/releases" in app.openapi()["paths"]
    with TestClient(app) as client:
        response: Final = client.get("/releases")
        assert response.status_code == 503
        assert "尚未启用" in response.text


def test_mounted_release_router_uses_separate_worker_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCOUNT_POOL_RELEASE_TOKEN", "s" * 32)
    app: Final = FastAPI()
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(
        user_id="administrator", user_role=LitellmUserRoles.PROXY_ADMIN
    )

    def manager_client() -> AccountPoolManagerClient:
        pytest.fail("版本管理不能使用号池 Manager 客户端")

    def transport(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://release-worker:8092/api/releases/execute"
        assert request.headers["Authorization"] == "Bearer " + "s" * 32
        assert request.content == b'{"token":"' + b"a" * 64 + b'"}'
        return httpx.Response(409, json={"detail": "请等待确认倒计时结束"})

    app.include_router(
        create_account_pool_router(
            manager_client,
            release_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(transport)),
        )
    )
    with TestClient(app) as client:
        response: Final = client.post("/account_pool/releases/execute", json={"token": "a" * 64})
    assert response.status_code == 409
    assert response.json()["detail"] == "请等待确认倒计时结束"
