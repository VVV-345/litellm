"""验证 Cockpit 授权范围发布代理的管理员边界、固定路径与安全错误映射。"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Final
from uuid import UUID

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_endpoints import (
    AccountPoolManagerClient,
    ManagerClientFactory,
    create_account_pool_router,
)

TOKEN: Final = "m" * 32
SUBJECT: Final = "a" * 64
CARD: Final = UUID(int=1)
R1: Final = '"sha256-' + "b" * 64 + '"'
R2: Final = '"sha256-' + "c" * 64 + '"'


def _app(factory: ManagerClientFactory, user: UserAPIKeyAuth | None = None) -> FastAPI:
    app: Final = FastAPI()
    app.include_router(create_account_pool_router(factory))
    app.dependency_overrides[user_api_key_auth] = lambda: user or UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN)
    return app


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> AccountPoolManagerClient:
    return AccountPoolManagerClient(
        "http://manager.test", TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


def _view(*, subject: str = SUBJECT, state: str = "staged") -> dict[str, object]:
    return {
        "subject_hash": subject,
        "state": state,
        "account_ids": [str(CARD)],
        "model_scope": "restricted",
        "models": ["fixture-model"],
        "persisted_manifest_revision": R1,
        "runtime_activation": "restart_required_or_unverified",
        "ready_to_migrate": False,
    }


def test_scope_routes_require_proxy_admin_before_manager() -> None:
    calls: Final[list[httpx.Request]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_view())

    app: Final = _app(lambda: _client(handler), user=UserAPIKeyAuth(user_role=LitellmUserRoles.INTERNAL_USER))
    with TestClient(app) as client:
        read: Final = client.get("/account_pool/cockpit/scope/" + SUBJECT)
        publish: Final = client.post(
            "/account_pool/cockpit/scope", json={"subject_hash": SUBJECT, "receipt": "r", "revision": R1}
        )
        revoke: Final = client.request(
            "DELETE", "/account_pool/cockpit/scope", json={"subject_hash": SUBJECT, "revision": R1}
        )
    assert read.status_code == publish.status_code == revoke.status_code == 403
    assert calls == []


def test_scope_proxy_forwards_exact_requests_and_never_returns_a_key() -> None:
    calls: Final[list[httpx.Request]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_view())

    with TestClient(_app(lambda: _client(handler))) as client:
        read: Final = client.get("/account_pool/cockpit/scope/" + SUBJECT)
        publish: Final = client.post(
            "/account_pool/cockpit/scope", json={"subject_hash": SUBJECT, "receipt": "encoded", "revision": R1}
        )
        revoke: Final = client.request(
            "DELETE", "/account_pool/cockpit/scope", json={"subject_hash": SUBJECT, "revision": R1}
        )
    assert (read.status_code, publish.status_code, revoke.status_code) == (200, 200, 200)
    assert all(response.headers["cache-control"] == "no-store" for response in (read, publish, revoke))
    assert "key" not in read.json() and "api_key" not in read.json()
    assert [(request.method, request.url.path) for request in calls] == [
        ("GET", "/api/cockpit/scope/" + SUBJECT),
        ("POST", "/api/cockpit/scope"),
        ("DELETE", "/api/cockpit/scope"),
    ]
    assert json.loads(calls[1].content) == {"subject_hash": SUBJECT, "receipt": "encoded", "revision": R1}
    assert json.loads(calls[2].content) == {"subject_hash": SUBJECT, "revision": R1}


def test_scope_proxy_preserves_safe_conflict_and_rejects_unknown_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(409, json={"detail": "receipt_stale"})
        return httpx.Response(422, json={"detail": "PRIVATE-DETAIL"})

    with TestClient(_app(lambda: _client(handler))) as client:
        read: Final = client.get("/account_pool/cockpit/scope/" + SUBJECT)
        publish: Final = client.post(
            "/account_pool/cockpit/scope", json={"subject_hash": SUBJECT, "receipt": "encoded", "revision": R1}
        )
    assert read.status_code == 409 and read.json()["detail"] == "receipt_stale"
    assert publish.status_code == 503 and publish.json()["detail"] == "write_outcome_unknown"
    assert "PRIVATE" not in read.text + publish.text
    assert publish.headers["cache-control"] == "no-store"


def test_scope_proxy_rejects_manager_view_for_another_subject_or_missing_fields() -> None:
    def mismatch(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_view(subject="0" * 64))

    with TestClient(_app(lambda: _client(mismatch))) as client:
        response: Final = client.get("/account_pool/cockpit/scope/" + SUBJECT)
    assert response.status_code == 502 and response.json()["detail"] == "invalid_response"

    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"subject_hash": SUBJECT})

    with TestClient(_app(lambda: _client(missing))) as client:
        broken: Final = client.post(
            "/account_pool/cockpit/scope", json={"subject_hash": SUBJECT, "receipt": "r", "revision": R1}
        )
    assert broken.status_code == 502 and broken.json()["detail"] == "invalid_response"
