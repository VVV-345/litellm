"""验证 Manager 日志代理仅开放给 Proxy Admin，不透传原始诊断。"""

from typing import Final
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_endpoints import _require_proxy_admin
from litellm.proxy.management_endpoints.account_pool_management import create_management_router

PATH: Final = "/account_pool/manager-logs"


@pytest.mark.parametrize("role", [
    LitellmUserRoles.PROXY_ADMIN_VIEW_ONLY, LitellmUserRoles.INTERNAL_USER, LitellmUserRoles.ORG_ADMIN,
])
def test_manager_log_query_and_delete_require_write_capable_proxy_admin(role: LitellmUserRoles) -> None:
    manager: Final = AsyncMock()
    app: Final = FastAPI()
    app.include_router(create_management_router(manager, _require_proxy_admin), prefix="/account_pool")
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(user_role=role)
    with TestClient(app) as client:
        assert client.get(PATH).status_code == 403
        assert client.delete(PATH, params={"older_than_days": 7}).status_code == 403
    manager.assert_not_awaited()


def test_manager_log_filters_and_cleanup_forward_only_validated_fields() -> None:
    session: Final = str(uuid4())
    card: Final = str(uuid4())
    manager: Final = AsyncMock(side_effect=[
        httpx.Response(200, json={"items": [], "has_more": False}),
        httpx.Response(200, json={"deleted": 2}),
        httpx.Response(502, json={"detail": "fixture-password and raw private configuration"}),
    ])
    app: Final = FastAPI()
    app.include_router(create_management_router(manager, _require_proxy_admin), prefix="/account_pool")
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN)
    with TestClient(app) as client:
        result: Final = client.get(PATH, params={"session_id": session, "card_id": card, "limit": 20})
        assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
        assert manager.await_args.args == (
            "GET", f"/api/manager-logs?session_id={session}&card_id={card}&limit=20&offset=0", None,
        )
        assert client.get(PATH, params={"limit": 101}).status_code == 422
        assert client.delete(PATH).status_code == 422
        assert client.delete(PATH, params={"older_than_days": 7}).json() == {"deleted": 2}
        assert manager.await_args.args == ("DELETE", "/api/manager-logs?older_than_days=7", None)
        failure: Final = client.get(PATH)
        assert failure.status_code == 502 and "fixture-password" not in failure.text
        assert manager.await_count == 3
