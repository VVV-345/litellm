"""验证换绑代理的管理员边界、二次确认及 Manager 故障脱敏。"""

from typing import Final
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_email_rebinding import create_rebinding_router


@pytest.mark.parametrize("admin", [False, True])
def test_binding_import_and_confirmation_are_admin_only_without_echoing_input(admin: bool) -> None:
    manager: Final = AsyncMock(return_value=httpx.Response(502, json={"detail": "private-mail-password private-rt"}))

    def require_admin(user: UserAPIKeyAuth) -> None:
        if user.user_role != LitellmUserRoles.PROXY_ADMIN:
            raise HTTPException(403, "admin only")

    app: Final = FastAPI()
    app.include_router(create_rebinding_router(manager, require_admin), prefix="/account_pool")
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(
        user_role=LitellmUserRoles.PROXY_ADMIN if admin else LitellmUserRoles.INTERNAL_USER
    )
    with TestClient(app) as client:
        rejected: Final = client.post(
            f"/account_pool/email-rebinding/jobs/{uuid4()}/confirm", json={"version": 0, "confirmed": 1}
        )
        assert rejected.status_code == (422 if admin else 403)
        manager.assert_not_awaited()
        uploaded: Final = client.post(
            "/account_pool/email-rebinding/mailboxes", json={"content": "private-mail-password private-rt"}
        )
        assert uploaded.status_code == (502 if admin else 403)
        assert "private-mail-password" not in uploaded.text and "private-rt" not in uploaded.text
        if admin:
            assert uploaded.headers["cache-control"] == "no-store"
            assert manager.await_args.args[1] == "/api/email-rebinding/mailboxes"
        else:
            manager.assert_not_awaited()
