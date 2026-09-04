"""本文件验证号池代理 API 的权限、管理器鉴权和响应边界。"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Final, TypedDict
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


class _ManagerEnvironment(TypedDict):
    id: str
    version: int
    desired_state: str
    operation_id: None
    desired_configuration_version: int
    observed_configuration_version: int
    name: str
    provider: str
    channel: str
    supplier: str
    configuration_pending: bool
    status: str
    enabled: bool
    manual_cooldown: bool
    concurrency_limit: int
    proxy_mode: str
    proxy_profile_id: None
    available_models: list[str]
    enabled_models: list[str]
    quota: dict[str, object]
    model_quotas: list[object]
    cooldown_until: None
    automatic_cooldown: bool
    last_error: None
    created_at: str
    updated_at: str


class _ManagerAuthorization(TypedDict):
    environment: _ManagerEnvironment
    flow: str
    authorization_url: str
    ssh_command: str | None
    user_code: str | None
    expires_at: str


def _authorization_response(environment: _ManagerEnvironment) -> _ManagerAuthorization:
    flow: Final = "browser_oauth"
    return {
        "environment": environment,
        "flow": flow,
        "authorization_url": "https://example.com/oauth",
        "ssh_command": "ssh -N example.com",
        "user_code": None,
        "expires_at": "2026-01-01T00:05:00Z",
    }


def _manager_response(request: httpx.Request) -> httpx.Response:
    environment: Final[_ManagerEnvironment] = {
        "id": str(_ENVIRONMENT_ID),
        "version": 3,
        "desired_state": "awaiting_authorization",
        "operation_id": None,
        "desired_configuration_version": 1,
        "observed_configuration_version": 1,
        "name": "Test environment",
        "provider": "openai",
        "channel": "cliproxyapi",
        "supplier": "openai_codex",
        "configuration_pending": False,
        "status": "awaiting_authorization",
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
        "automatic_cooldown": False,
        "last_error": None,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    forwarded_bodies: Final[list[dict[str, object]]] = []
    if request.url.path == "/api/environments" and request.method == "POST":
        forwarded_bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=_authorization_response(environment),
            request=request,
        )
    if request.url.path == f"/api/environments/{_ENVIRONMENT_ID}/authorize" and request.method == "POST":
        return httpx.Response(
            200,
            json=_authorization_response(environment),
            request=request,
        )
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
                    "channel": "cliproxyapi",
                    "supplier": "openai_codex",
                    "configuration_pending": False,
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


def test_proxy_admin_can_create_environment_from_manager_authorization_response() -> None:
    app: Final = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN), _manager_factory)

    with TestClient(app) as client:
        response: Final = client.post("/account_pool/environments", json={"name": "Test environment"})

    assert response.status_code == 200
    assert response.json()["environment"]["id"] == str(_ENVIRONMENT_ID)
    assert response.json()["flow"] == "browser_oauth"


def test_create_forwards_selected_channel_and_supplier_to_manager_unchanged() -> None:
    forwarded: dict[str, object] = {}

    def factory() -> AccountPoolManagerClient:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/environments" and request.method == "POST":
                forwarded.update(json.loads(request.content))
                return httpx.Response(200, json=_authorization_response(_ENVIRONMENT_FIXTURE), request=request)
            return httpx.Response(404, request=request)

        return AccountPoolManagerClient(
            "http://manager.test",
            _MANAGER_TOKEN,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    app: Final = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN), factory)

    with TestClient(app) as client:
        response: Final = client.post(
            "/account_pool/environments",
            json={"name": "Claude account", "channel": "cliproxyapi", "supplier": "anthropic_claude"},
        )

    assert response.status_code == 200
    assert forwarded == {
        "name": "Claude account",
        "provider": "openai",
        "channel": "cliproxyapi",
        "supplier": "anthropic_claude",
    }


def test_create_rejects_unknown_channel_and_supplier_values() -> None:
    app: Final = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN), _manager_factory)

    with TestClient(app) as client:
        bad_channel: Final = client.post(
            "/account_pool/environments", json={"name": "Test environment", "channel": "unknown"}
        )
        bad_supplier: Final = client.post(
            "/account_pool/environments", json={"name": "Test environment", "supplier": "unknown"}
        )

    assert bad_channel.status_code == 422
    assert bad_supplier.status_code == 422


def test_proxy_admin_can_read_channel_and_supplier_metadata() -> None:
    app: Final = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN), _manager_factory)

    with TestClient(app) as client:
        response: Final = client.get("/account_pool/environments")

    assert response.status_code == 200
    first: Final = response.json()[0]
    assert first["channel"] == "cliproxyapi"
    assert first["supplier"] == "openai_codex"
    assert first["configuration_pending"] is False


def test_malformed_manager_environment_response_is_rejected() -> None:
    def factory() -> AccountPoolManagerClient:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/environments":
                return httpx.Response(200, json=[{"id": "not-a-uuid"}], request=request)
            return httpx.Response(404, request=request)

        return AccountPoolManagerClient(
            "http://manager.test",
            _MANAGER_TOKEN,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    app: Final = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN), factory)

    with TestClient(app) as client:
        response: Final = client.get("/account_pool/environments")

    assert response.status_code == 502


_ENVIRONMENT_FIXTURE: Final = {
    "id": str(_ENVIRONMENT_ID),
    "version": 3,
    "desired_state": "awaiting_authorization",
    "operation_id": None,
    "desired_configuration_version": 1,
    "observed_configuration_version": 1,
    "name": "Test environment",
    "provider": "openai",
    "channel": "cliproxyapi",
    "supplier": "openai_codex",
    "configuration_pending": False,
    "status": "awaiting_authorization",
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
    "automatic_cooldown": False,
    "last_error": None,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}


def test_proxy_admin_can_reauthorize_environment_from_manager_response() -> None:
    app: Final = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN), _manager_factory)

    with TestClient(app) as client:
        response: Final = client.post(f"/account_pool/environments/{_ENVIRONMENT_ID}/authorize")

    assert response.status_code == 200
    assert response.json()["environment"]["id"] == str(_ENVIRONMENT_ID)
    assert response.json()["authorization_url"] == "https://example.com/oauth"


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
