"""验证 AdsPower 同步接口的管理员权限和脱敏错误。"""

from typing import Final, Literal

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_adspower import create_adspower_router


@pytest.mark.asyncio
@pytest.mark.parametrize("admin", (False, True))
async def test_adspower_proxy_checks_admin_and_forwards_pagination(admin: bool) -> None:
    def require_admin(user: UserAPIKeyAuth) -> None:
        if user.user_role != LitellmUserRoles.PROXY_ADMIN:
            raise HTTPException(403)

    async def manager(
        method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"], path: str, body: bytes | None = None
    ) -> httpx.Response:
        assert method == "GET"
        assert path == "/api/adspower/profiles?page=2"
        return httpx.Response(200, json={"page": 2, "has_more": False, "profiles": []})

    async def auth() -> UserAPIKeyAuth:
        return UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN if admin else LitellmUserRoles.INTERNAL_USER)

    app: Final = FastAPI()
    app.dependency_overrides[user_api_key_auth] = auth
    app.include_router(create_adspower_router(manager, require_admin))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy") as client:
        response: Final = await client.get("/adspower/profiles?page=2")
    assert response.status_code == (200 if admin else 403)
    if admin:
        assert response.json()["page"] == 2


@pytest.mark.asyncio
async def test_sync_error_hides_manager_response() -> None:
    async def manager(
        method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"], path: str, body: bytes | None = None
    ) -> httpx.Response:
        assert method == "POST"
        assert path == "/api/adspower/sync"
        assert body == b'{"profile_ids":["profile-1"]}'
        return httpx.Response(502, text="private-password")

    async def auth() -> UserAPIKeyAuth:
        return UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN)

    app: Final = FastAPI()
    app.dependency_overrides[user_api_key_auth] = auth
    app.include_router(create_adspower_router(manager, lambda _: None))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy") as client:
        response: Final = await client.post("/adspower/sync", json={"profile_ids": ["profile-1"]})
    assert response.status_code == 502
    assert "private-password" not in response.text
