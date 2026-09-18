from __future__ import annotations

from typing import Final, Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_management import create_management_router
from litellm.proxy.management_endpoints.account_pool_management_models import (
    AccountPoolSettings,
    AccountPoolSettingsUpdate,
    AccountPoolSettingsView,
)
from litellm.proxy.management_endpoints.request_log_endpoints import create_request_log_router


class SettingsManager:
    def __init__(self) -> None:
        self.settings = AccountPoolSettingsView(
            version=7,
            values=AccountPoolSettings(
                default_concurrency_limit=9,
                quota_refresh_interval_minutes=15,
                oauth_excluded_models=["private-model"],
            ),
            requires_reload=False,
        )
        self.writes: list[AccountPoolSettingsUpdate] = []

    async def request(
        self, method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"], path: str, body: bytes | None
    ) -> httpx.Response:
        if method == "PUT":
            request: Final = AccountPoolSettingsUpdate.model_validate_json(body or b"")
            self.writes.append(request)
            self.settings = AccountPoolSettingsView(version=8, values=request.values, requires_reload=True)
        return httpx.Response(200, content=self.settings.model_dump_json())


def authorize(user: object) -> None:
    if user != "admin":
        raise HTTPException(403)


def test_log_settings_preserve_account_policy_and_reject_stale_updates() -> None:
    manager: Final = SettingsManager()
    app: Final = FastAPI()
    app.include_router(create_request_log_router(manager.request, authorize))
    app.dependency_overrides[user_api_key_auth] = lambda: "admin"
    with TestClient(app) as client:
        current: Final = client.get("/logs/settings")
        assert current.status_code == 200
        assert current.headers["cache-control"] == "no-store"
        assert "oauth_excluded_models" not in current.json()["values"]
        updated: Final = {**current.json(), "values": {**current.json()["values"], "full_logging_enabled": True}}
        saved: Final = client.put("/logs/settings", json=updated)
        assert saved.status_code == 200
        assert saved.json()["requires_reload"] is True
        assert manager.writes[0].values.full_logging_enabled
        assert manager.writes[0].values.default_concurrency_limit == 9
        assert manager.writes[0].values.quota_refresh_interval_minutes == 15
        assert manager.writes[0].values.oauth_excluded_models == ("private-model",)
        assert client.put("/logs/settings", json=updated).status_code == 409
        assert len(manager.writes) == 1


def test_migrated_routes_require_admin_and_pool_no_longer_owns_logs() -> None:
    manager: Final = SettingsManager()
    app: Final = FastAPI()
    app.include_router(create_request_log_router(manager.request, authorize))
    app.include_router(create_management_router(manager.request, authorize), prefix="/account_pool")
    app.dependency_overrides[user_api_key_auth] = lambda: "viewer"
    with TestClient(app) as client:
        for path in ("/logs/settings", "/logs/operations", "/logs/full"):
            assert client.get(path).status_code == 403
        for path in ("/account_pool/logs", "/account_pool/full-logs", "/account_pool/stats"):
            assert client.get(path).status_code == 404
