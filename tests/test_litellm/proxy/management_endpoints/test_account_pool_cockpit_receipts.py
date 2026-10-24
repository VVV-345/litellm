"""验证 LiteLLM 主体回调和公开回执路由的隔离边界。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Final
from unittest.mock import patch
from uuid import UUID

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_cockpit_subject import NativeModelScope, NativeSubjectResult
from litellm.proxy.management_endpoints.account_pool_endpoints import (
    AccountPoolManagerClient,
    ManagerClientFactory,
    create_account_pool_router,
)

TOKEN: Final = "m" * 32
SUBJECT: Final = "a" * 64
CARD: Final = UUID(int=1)


def _app(
    factory: ManagerClientFactory,
    *,
    reader: Callable[[str], Awaitable[NativeSubjectResult]] | None = None,
    user: UserAPIKeyAuth | None = None,
) -> FastAPI:
    app: Final = FastAPI()
    app.include_router(create_account_pool_router(factory, native_subject_reader=reader or _unavailable_reader))
    app.dependency_overrides[user_api_key_auth] = lambda: user or UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN)
    return app


async def _unavailable_reader(_: str) -> NativeSubjectResult:
    return NativeSubjectResult(status="unavailable", checked_at=datetime.now(timezone.utc))


def _client_factory(handler: Callable[[httpx.Request], httpx.Response]) -> AccountPoolManagerClient:
    return AccountPoolManagerClient(
        "http://manager.test", TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


def test_internal_subject_requires_service_bearer_and_reads_verified_scope_only(
) -> None:
    calls: Final[list[str]] = []

    async def reader(subject_hash: str) -> NativeSubjectResult:
        calls.append(subject_hash)
        return NativeSubjectResult(
            status="verified",
            card_id=CARD,
            model_scope=NativeModelScope(kind="restricted", models=("fixture-model",)),
            checked_at=datetime.now(timezone.utc),
        )

    app: Final = _app(lambda: _client_factory(lambda _: httpx.Response(404)), reader=reader)
    with patch.dict("os.environ", {"ACCOUNT_POOL_MANAGER_TOKEN": TOKEN}), TestClient(app) as client:
        denied: Final = client.post(
            "/account_pool/internal/cockpit/native-subject",
            headers={"Authorization": "Bearer user-token"},
            json={"subject_hash": SUBJECT},
        )
        invalid: Final = client.post(
            "/account_pool/internal/cockpit/native-subject",
            headers={"Authorization": "Bearer " + TOKEN},
            json={"subject_hash": SUBJECT, "scope": {"card_id": str(CARD)}},
        )
        verified: Final = client.post(
            "/account_pool/internal/cockpit/native-subject",
            headers={"Authorization": "Bearer " + TOKEN},
            json={"subject_hash": SUBJECT},
        )
    assert denied.status_code == 401 and invalid.status_code == 422
    assert all(response.headers["cache-control"] == "no-store" for response in (denied, invalid, verified))
    assert verified.status_code == 200
    assert verified.json() == {
        "subject_hash": SUBJECT,
        "scope": {
            "subject_hash": SUBJECT,
            "card_id": str(CARD),
            "model_scope": "restricted",
            "models": ["fixture-model"],
        },
    }
    assert calls == [SUBJECT]


def test_public_receipt_routes_require_admin_before_manager_and_redact_validation(
) -> None:
    calls: Final[list[httpx.Request]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "key_hash": SUBJECT,
                "status": "rejected",
                "ready_to_migrate": False,
                "receipt": None,
                "code": "subject_unverified",
            },
            request=request,
        )

    body: Final = {"key_hash": SUBJECT}
    with patch.dict("os.environ", {"ACCOUNT_POOL_MANAGER_TOKEN": TOKEN}), TestClient(
        _app(
            lambda: _client_factory(handler),
            user=UserAPIKeyAuth(user_role=LitellmUserRoles.INTERNAL_USER),
        )
    ) as client:
        forbidden: Final = client.post("/account_pool/cockpit/credential-receipts", json=body)
    assert forbidden.status_code == 403
    assert forbidden.headers["cache-control"] == "no-store"
    assert calls == []

    with patch.dict("os.environ", {"ACCOUNT_POOL_MANAGER_TOKEN": TOKEN}), TestClient(
        _app(lambda: _client_factory(handler))
    ) as client:
        invalid: Final = client.post(
            "/account_pool/cockpit/credential-receipts/revalidate",
            json={"key_hash": SUBJECT, "receipt": "PRIVATE" * 20_000},
        )
        accepted: Final = client.post("/account_pool/cockpit/credential-receipts", json=body)
    assert invalid.status_code == 422
    assert invalid.headers["cache-control"] == "no-store"
    assert "PRIVATE" not in invalid.text
    assert accepted.status_code == 200 and accepted.json()["status"] == "rejected"
    assert accepted.headers["cache-control"] == "no-store"
    assert len(calls) == 1 and json.loads(calls[0].content) == body
