"""验证 LiteLLM 上号入口要求出站代理并透传所选 Profile。"""

from __future__ import annotations

import json
from typing import Final
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_onboarding import create_onboarding_router


@pytest.mark.parametrize(("proxy_profile_id", "expected_status"), ((None, 422), ("proxy-us", 200)))
def test_oauth_preview_requires_and_forwards_proxy_profile(proxy_profile_id: str | None, expected_status: int) -> None:
    manager: Final = AsyncMock(
        return_value=httpx.Response(
            200,
            json=[],
            request=httpx.Request("POST", "http://manager.test/api/onboarding/preview"),
        )
    )
    app: Final = FastAPI()
    app.include_router(create_onboarding_router(manager, lambda _user: None), prefix="/account_pool")
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN)
    body: Final = {
        "job_id": str(uuid4()),
        "source": "oauth",
        "supplier": "openai_codex",
        "mailbox": "gmail",
        "prepare_mailbox": True,
        "entries": [{"label": "account@example.com", "mailbox_password": "mail-secret"}],
        **({"proxy_profile_id": proxy_profile_id} if proxy_profile_id is not None else {}),
    }

    with TestClient(app) as client:
        response: Final = client.post("/account_pool/onboarding/preview", json=body)

    assert response.status_code == expected_status
    if proxy_profile_id is None:
        manager.assert_not_awaited()
        return
    manager.assert_awaited_once()
    forwarded_body: Final = manager.await_args.args[2]
    assert isinstance(forwarded_body, bytes)
    assert json.loads(forwarded_body)["proxy_profile_id"] == proxy_profile_id
