"""验证自动授权管理边界，登录资料仅转发到 Manager，不进入错误响应。"""

from typing import Final
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_automation import create_automation_router


@pytest.mark.parametrize("admin", (True, False))
def test_automation_requires_admin_and_redacts_invalid_secret_input(admin: bool) -> None:
    manager: Final = AsyncMock()

    def require_admin(user: UserAPIKeyAuth) -> None:
        if user.user_role != LitellmUserRoles.PROXY_ADMIN:
            raise HTTPException(403, "admin only")

    app: Final = FastAPI()
    app.include_router(create_automation_router(manager, require_admin), prefix="/account_pool")
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN if admin else LitellmUserRoles.INTERNAL_USER)
    with TestClient(app) as client:
        response: Final = client.post("/account_pool/oauth-automation/jobs", json={"credentials": "private-password----TOTP"})
    assert response.status_code == (422 if admin else 403)
    assert "private-password" not in response.text
    manager.assert_not_awaited()


def test_worker_errors_never_echo_credentials_or_internal_paths() -> None:
    manager: Final = AsyncMock(return_value=httpx.Response(502, json={"detail": "private-password /internal/worker"}))
    app: Final = FastAPI()
    app.include_router(create_automation_router(manager, lambda user: None), prefix="/account_pool")
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN)
    with TestClient(app) as client:
        response: Final = client.post("/account_pool/oauth-automation/jobs", json={"id": str(uuid4()), "environment_id": str(uuid4()), "credentials": "one@example.test----private-password----GEZDGNBVGY3TQOJQ"})
    assert response.status_code == 502
    assert response.headers["cache-control"] == "no-store"
    assert "private-password" not in response.text and "/internal/worker" not in response.text
    assert manager.await_args.args[1] == "/api/oauth-automation/jobs"


def test_proxy_matching_defaults_and_matched_location_survive_gateway_forwarding() -> None:
    profile: Final = {
        "id": str(uuid4()), "name": "fixed", "enabled": True, "locale": "en-US", "auto_match_proxy": True,
        "version": 1, "digest": "a" * 64, "bound_environment_id": str(uuid4()), "matched_locale": "ja-JP",
        "location": {"exit_ip": "8.8.8.8", "country_code": "JP", "country": "Japan", "timezone": "Asia/Tokyo"},
    }
    manager: Final = AsyncMock(return_value=httpx.Response(200, json=profile))
    app: Final = FastAPI()
    app.include_router(create_automation_router(manager, lambda user: None), prefix="/account_pool")
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN)
    with TestClient(app) as client:
        response: Final = client.post("/account_pool/oauth-automation/profiles", json={"name": "fixed"})
    assert response.status_code == 201
    assert response.json()["matched_locale"] == "ja-JP"
    assert response.json()["location"]["timezone"] == "Asia/Tokyo"
    import json
    assert json.loads(manager.await_args.args[2])["auto_match_proxy"] is True


def test_profile_language_presets_and_manual_input_survive_gateway_forwarding() -> None:
    profile = {
        "id": str(uuid4()), "name": "fixed", "locale": "uk-UA", "auto_match_proxy": False, "digest": "a" * 64,
    }
    view = {
        "version": 1, "settings": {}, "profiles": [profile], "jobs": [], "checked_at": None,
        "image_checked": True, "locale_presets": ["en-US", "uk-UA", "it-IT"],
    }
    manager = AsyncMock(side_effect=[httpx.Response(200, json=view), httpx.Response(201, json=profile)])
    app = FastAPI()
    app.include_router(create_automation_router(manager, lambda user: None), prefix="/account_pool")
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN)

    with TestClient(app) as client:
        loaded = client.get("/account_pool/oauth-automation")
        created = client.post("/account_pool/oauth-automation/profiles", json={
            "name": "fixed", "locale": "uk-UA", "auto_match_proxy": False,
        })
        invalid = client.post("/account_pool/oauth-automation/profiles", json={"name": "invalid", "locale": "uk_UA"})
    assert loaded.status_code == 200
    assert loaded.json()["locale_presets"] == ["en-US", "uk-UA", "it-IT"]
    assert created.status_code == 201 and created.json()["locale"] == "uk-UA"
    assert invalid.status_code == 422 and manager.await_count == 2


@pytest.mark.parametrize("admin", [False, True])
def test_automation_job_delete_is_admin_only_and_targets_the_same_record(admin):
    manager = AsyncMock(return_value=httpx.Response(409, json={"detail": "private job data"}))
    job_id = uuid4()

    def require_admin(user):
        if user.user_role != LitellmUserRoles.PROXY_ADMIN:
            raise HTTPException(403, "admin only")

    app = FastAPI()
    app.include_router(create_automation_router(manager, require_admin), prefix="/account_pool")
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(
        user_role=LitellmUserRoles.PROXY_ADMIN if admin else LitellmUserRoles.INTERNAL_USER
    )
    with TestClient(app) as client:
        result = client.delete(f"/account_pool/oauth-automation/jobs/{job_id}")
    assert result.status_code == (409 if admin else 403)
    assert "private job data" not in result.text
    if admin:
        manager.assert_awaited_once_with("DELETE", f"/api/oauth-automation/jobs/{job_id}", None)
    else:
        manager.assert_not_awaited()


@pytest.mark.parametrize(("role", "manager_status", "expected"), [
    (LitellmUserRoles.INTERNAL_USER, 202, 403),
    (LitellmUserRoles.PROXY_ADMIN, 202, 202),
    (LitellmUserRoles.PROXY_ADMIN, 409, 409),
])
def test_reauthorize_requests_saved_details_for_the_same_card(role, manager_status, expected):
    manager = AsyncMock(return_value=httpx.Response(
        manager_status, content=b"null" if manager_status == 202 else b'{"detail":"private fixture"}',
    ))
    card_id = uuid4()

    def require_admin(user):
        if user.user_role != LitellmUserRoles.PROXY_ADMIN:
            raise HTTPException(403, "admin only")

    app = FastAPI()
    app.include_router(create_automation_router(manager, require_admin), prefix="/account_pool")
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(user_role=role)
    with TestClient(app) as client:
        result = client.post(f"/account_pool/oauth-automation/environments/{card_id}/reauthorize")
    assert result.status_code == expected
    if expected == 403:
        manager.assert_not_awaited()
    else:
        manager.assert_awaited_once_with("POST", f"/api/oauth-automation/environments/{card_id}/reauthorize", None)
        assert result.headers["cache-control"] == "no-store"
        if expected == 202:
            assert result.json() is None
        else:
            assert "private fixture" not in result.text


@pytest.mark.parametrize("status,message,code", [
    (422, "请选择一条 email----password----TOTPBase32 登录资料，不接收多账号混合记录", "invalid_credentials"),
    (502, "浏览器镜像自检失败；检查镜像、显示服务和预装资源，不会启动账号登录", "image_check_failed"),
])
def test_safe_oauth_failure_keeps_operator_reason_without_raw_details(status, message, code):
    manager = AsyncMock(return_value=httpx.Response(status, json={"detail": message}))
    app = FastAPI()
    app.include_router(create_automation_router(manager, lambda user: None), prefix="/account_pool")
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN)
    with TestClient(app) as client:
        response = client.get("/account_pool/oauth-automation")
    assert response.status_code == status
    assert response.json()["detail"] == {"code": code, "message": message}
    assert response.headers["cache-control"] == "no-store"



def test_local_automation_validation_returns_safe_structured_reason_without_input():
    manager = AsyncMock()
    app = FastAPI()
    app.include_router(create_automation_router(manager, lambda user: None), prefix="/account_pool")
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(user_role=LitellmUserRoles.PROXY_ADMIN)
    with TestClient(app) as client:
        response = client.post("/account_pool/oauth-automation/jobs", json={"credentials": "private-test-input"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"
    assert response.headers["cache-control"] == "no-store"
    assert "private-test-input" not in response.text
    manager.assert_not_awaited()
