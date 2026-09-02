"""本文件验证号池代理 API 的权限、管理器鉴权和响应边界。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_endpoints import (
    AccountPoolManagerClient,
    create_account_pool_router,
)

_MANAGER_TOKEN: Final = "m" * 32
_ENVIRONMENT_ID: Final = uuid4()


def _manager_response(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/environments":
        return httpx.Response(
            200,
            json=[
                {
                    "id": str(_ENVIRONMENT_ID),
                    "version": 3,
                    "desired_state": "ready",
                    "operation_id": None,
                    "desired_configuration_version": 1,
                    "observed_configuration_version": 1,
                    "name": "Test environment",
                    "provider": "openai",
                    "status": "ready",
                    "enabled": True,
                    "manual_cooldown": False,
                    "concurrency_limit": 2,
                    "proxy_mode": "default_gateway",
                    "proxy_profile_id": None,
                    "available_models": ["gpt-5"],
                    "enabled_models": ["gpt-5"],
                    "quota": {"observed_at": None, "plan_type": None, "windows": []},
                    "model_quotas": [],
                    "cooldown_until": None,
                    "automatic_cooldown": True,
                    "last_error": None,
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            ],
            request=request,
        )
    return httpx.Response(404, request=request)


def _app(user: UserAPIKeyAuth, manager_factory: Callable[[], AccountPoolManagerClient]) -> FastAPI:
    app: Final = FastAPI()
    app.include_router(create_account_pool_router(manager_factory))
    app.dependency_overrides[user_api_key_auth] = lambda: user
    return app


def _manager_factory() -> AccountPoolManagerClient:
    return AccountPoolManagerClient(
        "http://manager.test",
        _MANAGER_TOKEN,
        client=httpx.AsyncClient(transport=httpx.MockTransport(_manager_response)),
    )


def test_proxy_admin_can_read_automatic_cooldown_metadata() -> None:
    app: Final = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN), _manager_factory)

    with TestClient(app) as client:
        response: Final = client.get("/account_pool/environments")

    assert response.status_code == 200
    assert response.json()[0]["automatic_cooldown"] is True


def test_proxy_admin_viewer_cannot_read_or_manage_account_pool() -> None:
    app: Final = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN_VIEW_ONLY), _manager_factory)

    with TestClient(app) as client:
        response: Final = client.get("/account_pool/environments")

    assert response.status_code == 403
    assert response.json()["detail"] == "Only proxy admins can manage the account pool"


@pytest.mark.asyncio
async def test_manager_client_rejects_missing_or_short_token() -> None:
    client: Final = AccountPoolManagerClient(
        "http://manager.test",
        "short",
        client=httpx.AsyncClient(transport=httpx.MockTransport(_manager_response)),
    )

    with pytest.raises(Exception) as raised:
        await client.request("GET", "/api/environments")

    assert getattr(raised.value, "status_code", None) == 503
    await client.close()
