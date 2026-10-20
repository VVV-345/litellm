"""验证号池管理代理路由的方法、路径与响应契约。"""

from __future__ import annotations

from typing import Final, Literal
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_management import create_management_router
from litellm.proxy.management_endpoints.account_pool_management_models import PolicyView


class PolicyManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.card_id: Final = uuid4()
        self.policy: Final = PolicyView(card_id=self.card_id, version=1)

    async def request(
        self, method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"], path: str, body: bytes | None
    ) -> httpx.Response:
        self.calls.append((method, path))
        view: Final = (
            PolicyView(card_id=self.card_id) if method == "DELETE" else self.policy
        )
        return httpx.Response(200, content=view.model_dump_json())


def authorize(user: object) -> None:
    if user != "admin":
        raise HTTPException(403)


def _app(user: object, manager: PolicyManager) -> FastAPI:
    app: Final = FastAPI()
    app.include_router(create_management_router(manager.request, authorize), prefix="/account_pool")
    app.dependency_overrides[user_api_key_auth] = lambda: user
    return app


def test_reset_policy_forwards_delete_and_returns_the_default_view() -> None:
    manager: Final = PolicyManager()

    with TestClient(_app("admin", manager)) as client:
        response: Final = client.delete(f"/account_pool/environments/{manager.card_id}/policy")

    assert response.status_code == 200
    assert response.json()["version"] == 0
    assert manager.calls == [("DELETE", f"/api/environments/{manager.card_id}/policy")]


def test_reset_policy_requires_admin() -> None:
    manager: Final = PolicyManager()

    with TestClient(_app("viewer", manager)) as client:
        response: Final = client.delete(f"/account_pool/environments/{manager.card_id}/policy")

    assert response.status_code == 403
    assert manager.calls == []
