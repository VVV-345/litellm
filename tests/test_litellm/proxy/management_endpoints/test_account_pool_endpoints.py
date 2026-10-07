"""本文件验证号池代理 API 的权限、管理器鉴权和响应边界。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Final, TypedDict
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_endpoints import (
    AccountPoolManagerClient,
    _browser_relay_url,
    _relay_browser_websocket,
    create_account_pool_router,
)
from litellm.proxy.management_endpoints.account_pool_management_models import AccountPolicy, ErrorLogRecord, ErrorStats
from litellm.proxy.management_endpoints.account_pool_observability import AccountPoolDashboardStats

_MANAGER_TOKEN: Final = "m" * 32
_ENVIRONMENT_ID: Final = uuid4()


class _BrowserWebSocket:
    def __init__(self) -> None:
        self._messages: list[dict[str, str]] = [
            {"type": "websocket.receive", "text": "hello"},
            {"type": "websocket.disconnect"},
        ]

    async def receive(self) -> dict[str, str]:
        return self._messages.pop(0)

    async def send_text(self, message: str) -> None:
        return None

    async def send_bytes(self, message: bytes) -> None:
        return None


class _BrowserRelay:
    def __init__(self) -> None:
        self.sent: list[str | bytes] = []

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        await asyncio.Future()
        return ""


def test_browser_relay_url_preserves_non_ticket_query_parameters() -> None:
    session_id: Final = uuid4()
    url: Final = _browser_relay_url(
        session_id,
        "vnc.html",
        (("ticket", "secret"), ("path", "websockify"), ("token", "a b")),
    )

    assert url == (
        f"http://account-pool-oauth-browser-{session_id.hex}-callback-relay:8093/vnc.html?path=websockify&token=a+b"
    )


@pytest.mark.asyncio
async def test_browser_relay_cancels_the_other_direction_after_browser_disconnect() -> None:
    browser: Final = _BrowserWebSocket()
    relay: Final = _BrowserRelay()

    await _relay_browser_websocket(browser, relay)

    assert relay.sent == ["hello"]


def test_retired_channel_logs_remain_parseable_at_the_proxy_boundary() -> None:
    current: Final = ErrorLogRecord(
        channel="cliproxyapi",
        supplier="openai_codex",
        card_id=_ENVIRONMENT_ID,
        environment_id=_ENVIRONMENT_ID,
        account_id=_ENVIRONMENT_ID,
        operation="configuration",
        stage="configuration",
        message="Historical event",
    )
    payload: Final = {
        **current.model_dump(mode="json"),
        "channel": "freebuff2api",
        "supplier": "freebuff",
    }

    restored: Final = ErrorLogRecord.model_validate(payload)

    assert restored.channel == "freebuff2api"
    assert restored.supplier == "freebuff"


def test_legacy_desktop_policy_fields_are_ignored_at_the_proxy_boundary() -> None:
    policy: Final = AccountPolicy.model_validate(
        {
            "codex": {
                "responses_compact_enabled": True,
                "compact_ui": True,
                "model_context_window": 200000,
                "model_auto_compact_token_limit": 180000,
                "experimental_context_management": True,
            }
        }
    )

    assert policy.codex is not None
    assert policy.codex.responses_compact_enabled is True
    assert set(policy.codex.model_dump()) == {
        "identity_fingerprint_mode",
        "cli_only",
        "allow_app_server",
        "allow_app_server_clients",
        "responses_compact_enabled",
        "identity_confuse",
        "disable_codex_cloaking",
    }


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


@pytest.mark.parametrize("replace", [None, "true"])
def test_auth_file_upload_forwards_explicit_replacement_and_preserves_conflict(replace: str | None) -> None:
    def factory() -> AccountPoolManagerClient:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/auth-files"
            assert b'name="replace"\r\n\r\n' + (replace or "false").encode() in request.content
            return httpx.Response(409, json={"detail": "card already has a credential"}, request=request)

        return AccountPoolManagerClient(
            "http://manager.test", _MANAGER_TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )

    user: Final = UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN)
    form: Final = {"card_id": str(_ENVIRONMENT_ID), **({"replace": replace} if replace else {})}
    with TestClient(_app(user, factory)) as client:
        response: Final = client.post(
            "/account_pool/auth-files", data=form, files={"file": ("auth.json", b"{}", "application/json")}
        )
    assert response.status_code == 409
    assert "card already has a credential" in response.text


def test_proxy_forwards_auth_file_refresh_controls() -> None:
    requests: Final[list[tuple[str, str, bytes]]] = []

    def factory() -> AccountPoolManagerClient:
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append((request.method, request.url.path, request.content))
            return httpx.Response(200, json={"interval_minutes": 15}, request=request)

        return AccountPoolManagerClient(
            "http://manager.test",
            _MANAGER_TOKEN,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    app: Final = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN), factory)

    with TestClient(app) as client:
        status: Final = client.get("/account_pool/auth-files/refresh/status")
        updated: Final = client.put("/account_pool/auth-files/refresh/interval", json={"interval_minutes": 30})
        refreshed: Final = client.post("/account_pool/auth-files/refresh")

    assert status.status_code == 200
    assert status.json()["interval_minutes"] == 15
    assert updated.status_code == 200
    assert refreshed.status_code == 200
    assert requests == [
        ("GET", "/api/auth-files/refresh/status", b""),
        ("PUT", "/api/auth-files/refresh/interval", b'{"interval_minutes":30}'),
        ("POST", "/api/auth-files/refresh", b""),
    ]


def test_proxy_forwards_oauth_browser_session_lifecycle_without_exposing_manager_token() -> None:
    session_id: Final = uuid4()
    start_payload: Final = {
        "id": str(session_id),
        "environment_id": str(_ENVIRONMENT_ID),
        "status": "active",
        "created_at": "2026-01-01T00:00:00Z",
        "expires_at": "2026-01-01T00:10:00Z",
        "ticket": "browser-ticket-secret",
    }
    view_payload: Final = {key: value for key, value in start_payload.items() if key != "ticket"}
    calls: Final[list[tuple[str, str, str]]] = []

    def factory() -> AccountPoolManagerClient:
        def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path, request.headers.get("authorization", "")))
            if request.method == "POST":
                return httpx.Response(201, json=start_payload, request=request)
            return httpx.Response(200, json=view_payload, request=request)

        return AccountPoolManagerClient(
            "http://manager.test",
            _MANAGER_TOKEN,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    app: Final = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN), factory)
    with TestClient(app) as client:
        started: Final = client.post(f"/account_pool/environments/{_ENVIRONMENT_ID}/oauth-browser-sessions")
        observed: Final = client.get(f"/account_pool/oauth-browser-sessions/{session_id}")
        cancelled: Final = client.delete(f"/account_pool/oauth-browser-sessions/{session_id}")

    assert started.status_code == 200
    assert started.json()["ticket"] == "browser-ticket-secret"
    assert observed.status_code == 200
    assert cancelled.status_code == 200
    assert all(token == f"Bearer {_MANAGER_TOKEN}" for _, _, token in calls)


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
            json={
                "name": "Claude account",
                "channel": "cliproxyapi",
                "supplier": "anthropic_claude",
                "proxy_profile_id": "proxy-us",
            },
        )

    assert response.status_code == 200
    assert forwarded == {
        "name": "Claude account",
        "provider": "openai",
        "channel": "cliproxyapi",
        "supplier": "anthropic_claude",
        "proxy_profile_id": "proxy-us",
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


def test_xai_direct_credential_is_forwarded_to_manager() -> None:
    forwarded: Final[dict[str, object]] = {}

    def factory() -> AccountPoolManagerClient:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/direct-credentials" and request.method == "POST":
                forwarded.update(json.loads(request.content))
                return httpx.Response(200, json=_ENVIRONMENT_FIXTURE, request=request)
            return httpx.Response(404, request=request)

        return AccountPoolManagerClient(
            "http://manager.test",
            _MANAGER_TOKEN,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    app: Final = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN), factory)
    with TestClient(app) as client:
        response: Final = client.post(
            "/account_pool/direct-credentials",
            json={"name": "xAI API", "supplier": "xai", "credential": {"api_key": "secret"}},
        )

    assert response.status_code == 200
    assert forwarded["supplier"] == "xai"


def test_vertex_creation_forwards_idempotency_key_to_manager() -> None:
    forwarded_key: list[str | None] = []

    def factory() -> AccountPoolManagerClient:
        def handler(request: httpx.Request) -> httpx.Response:
            forwarded_key.append(request.headers.get("Idempotency-Key"))
            return httpx.Response(200, json=_ENVIRONMENT_FIXTURE, request=request)

        return AccountPoolManagerClient(
            "http://manager.test",
            _MANAGER_TOKEN,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    app: Final = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN), factory)

    with TestClient(app) as client:
        response: Final = client.post(
            "/account_pool/vertex",
            data={"name": "Vertex account", "location": "us-central1"},
            files={"file": ("service-account.json", b'{"project_id":"demo"}', "application/json")},
            headers={"Idempotency-Key": "operation-123"},
        )

    assert response.status_code == 200
    assert forwarded_key == ["operation-123"]


def test_proxy_admin_can_read_channel_and_supplier_metadata() -> None:
    app: Final = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN), _manager_factory)

    with TestClient(app) as client:
        response: Final = client.get("/account_pool/environments")

    assert response.status_code == 200
    first: Final = response.json()[0]
    assert first["channel"] == "cliproxyapi"
    assert first["supplier"] == "openai_codex"
    assert first["configuration_pending"] is False


@pytest.mark.parametrize("role", (LitellmUserRoles.PROXY_ADMIN, LitellmUserRoles.INTERNAL_USER))
def test_dashboard_uses_standard_statistics_and_requires_admin(role: LitellmUserRoles) -> None:
    def factory() -> AccountPoolManagerClient:
        pytest.fail("Standard statistics must not depend on the account manager")

    async def read_dashboard() -> AccountPoolDashboardStats:
        assert role == LitellmUserRoles.PROXY_ADMIN
        return AccountPoolDashboardStats(
            summary=ErrorStats(total_requests=4, succeeded_requests=3, failed_requests=1, statistics_source="litellm"),
            cards=(),
            occurred_from=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )

    app: Final = FastAPI()
    app.include_router(create_account_pool_router(factory, dashboard_reader=read_dashboard))
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(user_role=role)

    with TestClient(app) as client:
        response: Final = client.get("/account_pool/dashboard")

    assert response.status_code == (200 if role == LitellmUserRoles.PROXY_ADMIN else 403)
    if role == LitellmUserRoles.PROXY_ADMIN:
        assert response.json()["summary"]["total_requests"] == 4
        assert response.json()["statistics_source"] == "litellm"


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


def test_proxy_admin_can_submit_a_delete_batch() -> None:
    forwarded: dict[str, object] = {}
    job_id: Final = uuid4()

    def factory() -> AccountPoolManagerClient:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/batches" and request.method == "POST":
                forwarded.update(json.loads(request.content))
                return httpx.Response(
                    202,
                    json={
                        "job_id": str(job_id),
                        "action": "delete",
                        "created_at": "2026-09-10T00:00:00Z",
                        "items": [
                            {
                                "account_id": str(_ENVIRONMENT_ID),
                                "status": "queued",
                                "attempts": 0,
                                "message": None,
                                "finished_at": None,
                            }
                        ],
                    },
                    request=request,
                )
            return httpx.Response(404, request=request)

        return AccountPoolManagerClient(
            "http://manager.test",
            _MANAGER_TOKEN,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    app: Final = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN), factory)
    payload: Final = {
        "job_id": str(job_id),
        "action": "delete",
        "targets": [{"account_id": str(_ENVIRONMENT_ID), "version": 3, "policy_version": 0}],
        "policy": None,
    }

    with TestClient(app) as client:
        response: Final = client.post("/account_pool/batches", json=payload)

    assert response.status_code == 202
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["action"] == "delete"
    assert forwarded == payload


def test_proxy_admin_can_read_batch_authorization_details() -> None:
    job_id: Final = uuid4()

    def factory() -> AccountPoolManagerClient:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/batches" and request.method == "GET":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "job_id": str(job_id),
                            "action": "authorize",
                            "created_at": "2026-09-10T00:00:00Z",
                            "items": [
                                {
                                    "account_id": str(_ENVIRONMENT_ID),
                                    "status": "succeeded",
                                    "attempts": 1,
                                    "message": "Authorization details generated",
                                    "authorization": {
                                        "flow": "device_code",
                                        "authorization_url": "https://example.com/device",
                                        "ssh_command": None,
                                        "user_code": "ABCD-1234",
                                        "expires_at": "2026-09-10T00:05:00Z",
                                    },
                                    "finished_at": "2026-09-10T00:00:01Z",
                                }
                            ],
                        }
                    ],
                    request=request,
                )
            return httpx.Response(404, request=request)

        return AccountPoolManagerClient(
            "http://manager.test",
            _MANAGER_TOKEN,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    app: Final = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN), factory)

    with TestClient(app) as client:
        response: Final = client.get("/account_pool/batches")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()[0]["action"] == "authorize"
    assert response.json()[0]["items"][0]["authorization"]["user_code"] == "ABCD-1234"


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


def _gateway_factory(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[[], AccountPoolManagerClient]:
    def factory() -> AccountPoolManagerClient:
        return AccountPoolManagerClient(
            "http://manager.test",
            _MANAGER_TOKEN,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    return factory


def test_proxy_admin_can_list_and_switch_proxy_gateways() -> None:
    switch_bodies: Final[list[dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/proxy-gateways" and request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "port": 7891,
                        "profile_id": "clash-gateway-7891",
                        "name": "Clash 端口 7891",
                        "proxy_url": "http://host.docker.internal:7891",
                        "current_node": "美国01",
                    }
                ],
                request=request,
            )
        if request.url.path == "/api/proxy-gateways/7891" and request.method == "PUT":
            switch_bodies.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "port": 7891,
                    "profile_id": "clash-gateway-7891",
                    "name": "Clash 端口 7891",
                    "proxy_url": "http://host.docker.internal:7891",
                    "current_node": "日本02",
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    app: Final = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN), _gateway_factory(handler))

    with TestClient(app) as client:
        listing: Final = client.get("/account_pool/proxy-gateways")
        switched: Final = client.put("/account_pool/proxy-gateways/7891", json={"node_name": "日本02"})

    assert listing.status_code == 200
    assert listing.json()[0]["current_node"] == "美国01"
    assert switched.status_code == 200
    assert switched.json()["current_node"] == "日本02"
    assert switch_bodies == [{"node_name": "日本02"}]


def test_proxy_admin_can_read_proxy_gateway_configuration_location() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/proxy-gateways/configuration" and request.method == "GET":
            return httpx.Response(200, json={"config_path": "/opt/litellm/mihomo/config.yaml"}, request=request)
        return httpx.Response(404, request=request)

    app: Final = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN), _gateway_factory(handler))

    with TestClient(app) as client:
        response: Final = client.get("/account_pool/proxy-gateways/configuration")

    assert response.status_code == 200
    assert response.json() == {"config_path": "/opt/litellm/mihomo/config.yaml"}


@pytest.mark.parametrize("role", (LitellmUserRoles.PROXY_ADMIN, LitellmUserRoles.PROXY_ADMIN_VIEW_ONLY))
def test_proxy_gateway_delay_is_forwarded_only_for_admins(role: LitellmUserRoles) -> None:
    payload: Final = [
        {
            "port": 7891,
            "current_node": "US01",
            "status": "timeout",
            "delay_ms": None,
            "checked_at": "2026-09-08T12:00:00Z",
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert role == LitellmUserRoles.PROXY_ADMIN
        assert request.url.path == "/api/proxy-gateways/delay"
        assert request.method == "POST"
        assert request.headers["Authorization"] == f"Bearer {_MANAGER_TOKEN}"
        return httpx.Response(200, json=payload, request=request)

    app: Final = _app(UserAPIKeyAuth(user_role=role), _gateway_factory(handler))
    with TestClient(app) as client:
        response: Final = client.post("/account_pool/proxy-gateways/delay")

    assert response.status_code == (200 if role == LitellmUserRoles.PROXY_ADMIN else 403)
    if role == LitellmUserRoles.PROXY_ADMIN:
        assert response.json() == payload


def test_proxy_admin_can_list_clash_nodes_and_manager_errors_propagate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/proxy-gateways/nodes" and request.method == "GET":
            return httpx.Response(
                200,
                json=[{"name": "美国01", "proxy_type": "Shadowsocks"}, {"name": "日本02", "proxy_type": "Vmess"}],
                request=request,
            )
        if request.url.path == "/api/proxy-gateways" and request.method == "GET":
            return httpx.Response(502, json={"detail": "clash controller returned status 502"})
        return httpx.Response(404, request=request)

    app: Final = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN), _gateway_factory(handler))

    with TestClient(app) as client:
        nodes: Final = client.get("/account_pool/proxy-gateways/nodes")
        broken: Final = client.get("/account_pool/proxy-gateways")

    assert nodes.status_code == 200
    assert [node["name"] for node in nodes.json()] == ["美国01", "日本02"]
    assert broken.status_code == 502
    assert broken.json()["detail"] == "clash controller returned status 502"


def test_proxy_admin_can_manage_upstream_compatibility_workflow() -> None:
    requested: list[tuple[str, str]] = []
    report: Final = {
        "schema_version": 1,
        "state": "passed",
        "action": "analyze",
        "request_id": "af094d6b-f0da-4ad6-aa59-7704393a81a4",
        "target_tag": "v7.3.2",
        "base_sha": "e851070a0d08fb631d5ee7c64ecfab9a30ecbd2a",
        "candidate_sha": "92589ae0e0592e5469fb5f2e7859ab9664155419",
        "conflict_files": [],
        "failed_steps": [],
        "message": "passed",
        "workflow_url": "https://github.com/VVV-345/CLIProxyAPI/actions/runs/1",
        "updated_at": "2026-09-14T12:00:00Z",
    }

    def factory() -> AccountPoolManagerClient:
        def handler(request: httpx.Request) -> httpx.Response:
            requested.append((request.method, request.url.path))
            if request.url.path == "/api/upstream-sync":
                return httpx.Response(
                    200,
                    json={
                        "upstream_repository": "router-for-me/CLIProxyAPI",
                        "fork_repository": "VVV-345/CLIProxyAPI",
                        "sync_branch": "codex/upstream-sync",
                        "current_tag": "v7.2.146",
                        "latest_tag": "v7.3.2",
                        "latest_release_url": "https://github.com/router-for-me/CLIProxyAPI/releases/tag/v7.3.2",
                        "update_available": True,
                        "dispatch_configured": True,
                        "report": report,
                    },
                    request=request,
                )
            if request.url.path == "/api/upstream-sync/codex-review":
                return httpx.Response(
                    200,
                    json={
                        "filename": "codex-upstream-review-v7.3.2.md",
                        "branch": "codex/upstream-sync",
                        "target_tag": "v7.3.2",
                        "content": "# Review\n",
                    },
                    request=request,
                )
            return httpx.Response(
                202,
                json={
                    "request_id": "c24d4fcb-ff4a-424e-b86e-e3fe7a9ce649",
                    "action": "promote" if request.url.path.endswith("promote") else "analyze",
                    "target_tag": "v7.3.2",
                    "state": "queued",
                },
                request=request,
            )

        return AccountPoolManagerClient(
            "http://manager.test",
            _MANAGER_TOKEN,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    app: Final = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN), factory)

    with TestClient(app) as client:
        status_response: Final = client.get("/account_pool/upstream-sync")
        analysis_response: Final = client.post("/account_pool/upstream-sync/analyze")
        promotion_response: Final = client.post("/account_pool/upstream-sync/promote")
        review_response: Final = client.get("/account_pool/upstream-sync/codex-review")

    assert status_response.status_code == 200
    assert status_response.json()["report"]["state"] == "passed"
    assert analysis_response.status_code == 202
    assert analysis_response.json()["action"] == "analyze"
    assert promotion_response.status_code == 202
    assert promotion_response.json()["action"] == "promote"
    assert review_response.json()["branch"] == "codex/upstream-sync"
    assert requested == [
        ("GET", "/api/upstream-sync"),
        ("POST", "/api/upstream-sync/analyze"),
        ("POST", "/api/upstream-sync/promote"),
        ("GET", "/api/upstream-sync/codex-review"),
    ]


@pytest.mark.parametrize("root_path", ["", "/", "/luna", "/luna/"])
def test_browser_ticket_exchange_sets_scoped_cookie_without_fetching_worker(root_path: str) -> None:
    session_id = uuid4()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, request.headers.get("authorization")))
        return httpx.Response(
            200,
            json={
                "id": str(session_id),
                "environment_id": str(_ENVIRONMENT_ID),
                "status": "active",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            },
        )

    def factory() -> AccountPoolManagerClient:
        return AccountPoolManagerClient(
            "http://manager.test", _MANAGER_TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )

    app = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN), factory)
    with TestClient(app, base_url="https://testserver", root_path=root_path) as client:
        response = client.post(
            f"/account_pool/oauth-browser-sessions/{session_id}/browser",
            headers={"Authorization": "Bearer temporary-ticket"},
        )
    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    assert f"Path={root_path.rstrip('/')}/account_pool/oauth-browser-sessions/{session_id}/browser/" in cookie
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    assert response.headers["cache-control"] == "no-store"
    assert calls == [(f"/internal/oauth-browser-sessions/{session_id}/ticket/consume", "Bearer temporary-ticket")]
    assert response.content == b""


def test_browser_static_assets_keep_relative_paths_and_do_not_forward_credentials() -> None:
    session_id = uuid4()
    calls = []

    def manager(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/ticket/validate")
        assert request.headers["authorization"] == "Bearer temporary-ticket"
        return httpx.Response(
            200,
            json={
                "id": str(session_id),
                "environment_id": str(_ENVIRONMENT_ID),
                "status": "active",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            },
        )

    def relay(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=b"<html>noVNC</html>", headers={"Content-Type": "text/html; charset=utf-8"})

    app = FastAPI()
    app.include_router(
        create_account_pool_router(
            lambda: AccountPoolManagerClient(
                "http://manager.test", _MANAGER_TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(manager))
            ),
            browser_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(relay)),
        )
    )
    with TestClient(app) as client:
        response = client.get(
            f"/account_pool/oauth-browser-sessions/{session_id}/browser/vnc.html?resize=scale",
            headers={"Cookie": "account_pool_browser_ticket=temporary-ticket"},
        )
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    assert str(calls[0].url).endswith("/vnc.html?resize=scale")
    assert "authorization" not in calls[0].headers and "cookie" not in calls[0].headers


@pytest.mark.parametrize("origin", [None, "https://evil.example"])
def test_browser_websocket_rejects_missing_or_cross_origin_even_with_ticket(origin: str | None) -> None:
    session_id = uuid4()
    headers = {"Cookie": "account_pool_browser_ticket=temporary-ticket"}
    if origin:
        headers["Origin"] = origin
    app = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN), _manager_factory)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(
                f"/account_pool/oauth-browser-sessions/{session_id}/browser/websockify", headers=headers
            ):
                pass
    assert closed.value.code == 1008


def test_browser_query_ticket_cannot_authenticate_static_assets() -> None:
    app = _app(UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN), _manager_factory)
    with TestClient(app) as client:
        response = client.get(f"/account_pool/oauth-browser-sessions/{uuid4()}/browser/vnc.html?ticket=secret")
    assert response.status_code == 401


def test_browser_websocket_relays_binary_and_closes_after_upstream_disconnect() -> None:
    import threading

    from websockets.sync.server import serve

    import litellm.proxy.management_endpoints.account_pool_endpoints as endpoints

    session_id = uuid4()

    def manager(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": str(session_id),
                "environment_id": str(_ENVIRONMENT_ID),
                "status": "active",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
            },
        )

    def echo(connection):
        assert connection.recv() == b"RFB hello"
        connection.send(b"RFB reply")

    with serve(echo, "127.0.0.1", 0) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        port = server.socket.getsockname()[1]

        def dial(url: str):
            assert url == f"ws://account-pool-oauth-browser-{session_id.hex}-callback-relay:8093/websockify"
            return endpoints.connect(f"ws://127.0.0.1:{port}/websockify", proxy=None)

        app = FastAPI()
        app.include_router(
            create_account_pool_router(
                lambda: AccountPoolManagerClient(
                    "http://manager.test",
                    _MANAGER_TOKEN,
                    client=httpx.AsyncClient(transport=httpx.MockTransport(manager)),
                ),
                browser_websocket_connect=dial,
            )
        )
        with TestClient(app) as client:
            with client.websocket_connect(
                f"/account_pool/oauth-browser-sessions/{session_id}/browser/websockify",
                headers={"Origin": "http://testserver", "Cookie": "account_pool_browser_ticket=temporary-ticket"},
                subprotocols=["binary"],
            ) as websocket:
                assert websocket.accepted_subprotocol == "binary"
                websocket.send_bytes(b"RFB hello")
                assert websocket.receive_bytes() == b"RFB reply"
                with pytest.raises(WebSocketDisconnect):
                    websocket.receive_bytes()
        server.shutdown()
        worker.join(timeout=5)


def test_browser_start_has_timeout_for_worker_health_gate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.extensions["timeout"]["read"] == 120.0
        return httpx.Response(503)

    async def run():
        manager = AccountPoolManagerClient(
            "http://manager.test", _MANAGER_TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )
        await manager.request("POST", f"/api/environments/{_ENVIRONMENT_ID}/oauth-browser-sessions")

    asyncio.run(run())
