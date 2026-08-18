"""验证号池管理代理的管理员权限、内部令牌和响应脱敏。"""

from __future__ import annotations

from typing import Final

import httpx
import pytest
from fastapi import HTTPException, Request

from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.management_endpoints.account_pool_endpoints import (
    _forward_with_client,
    _require_proxy_admin,
)


def test_account_pool_management_rejects_non_admin() -> None:
    auth: Final = UserAPIKeyAuth(api_key="hashed", user_role=LitellmUserRoles.INTERNAL_USER)

    with pytest.raises(HTTPException) as error:
        _require_proxy_admin(auth)

    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_forward_adds_service_token_and_filters_upstream_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCOUNT_POOL_INTERNAL_URL", "http://account-pool:4100")
    monkeypatch.setenv("ACCOUNT_POOL_INTERNAL_TOKEN", "service-secret")
    captured: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            status_code=200,
            headers={"content-type": "application/json", "x-upstream-secret": "hidden"},
            json=[{"provider_id": "glm_official"}],
        )

    request: Final = Request({"type": "http", "method": "GET", "path": "/account_pool/provider-services"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        response: Final = await _forward_with_client(
            request=request,
            method="GET",
            path="/api/provider-services",
            client=client,
        )

    assert captured[0].headers["x-account-pool-token"] == "service-secret"
    assert str(captured[0].url) == "http://account-pool:4100/api/provider-services"
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert "x-upstream-secret" not in response.headers


@pytest.mark.asyncio
async def test_forward_fails_closed_without_service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACCOUNT_POOL_INTERNAL_TOKEN", raising=False)
    request: Final = Request({"type": "http", "method": "GET", "path": "/account_pool/accounts"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        with pytest.raises(HTTPException) as error:
            await _forward_with_client(request=request, method="GET", path="/api/accounts", client=client)

    assert error.value.status_code == 503
