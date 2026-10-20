"""验证原生配置模块保存的范围、并发冲突和管理员权限。"""

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_management_models import (
    AccountPoolSettings,
    AccountPoolSettingsUpdate,
    AccountPoolSettingsView,
)
from litellm.proxy.management_endpoints.runtime_configuration_endpoints import (
    create_runtime_configuration_router,
    merge_runtime_settings,
)


def test_streaming_save_preserves_quota_provider_proxy_logs_and_payload():
    settings = AccountPoolSettings(
        default_proxy_profile_id="production-proxy",
        quota_switch_project=True,
        oauth_model_aliases={"codex": (("source", "alias"),)},
        full_logging_enabled=True,
        request_timeout_seconds=333,
        max_attempts=4,
    )
    current = AccountPoolSettingsView(version=7, values=settings)
    submitted = AccountPoolSettingsUpdate(version=7, values=AccountPoolSettings(streaming_enabled=False))
    merged = merge_runtime_settings(current, submitted, "streaming")
    assert merged.values == settings.model_copy(update={"streaming_enabled": False})
    with pytest.raises(HTTPException) as error:
        merge_runtime_settings(current, submitted.model_copy(update={"version": 6}), "streaming")
    assert error.value.status_code == 409


def test_common_save_roundtrips_auth_file_template_without_touching_network():
    from account_pool.settings import AccountPoolSettings as ManagerSettings

    current = AccountPoolSettingsView(version=2, values=AccountPoolSettings(default_proxy_profile_id="keep"))
    template = {"priority": 0, "prefix": "team", "proxy_profile_id": "node-a"}
    patch = AccountPoolSettingsUpdate.model_validate({"version": 2, "values": {"auth_file_template": template}})
    merged = merge_runtime_settings(current, patch, "common")
    restored = ManagerSettings.model_validate_json(merged.values.model_dump_json())
    assert restored.model_dump()["auth_file_template"] == template
    assert restored.default_proxy_profile_id == "keep"


def test_common_save_without_template_preserves_previously_saved_template():
    current = AccountPoolSettingsView.model_validate({"version": 2, "values": {"auth_file_template": {"prefix": "keep"}}})
    patch = AccountPoolSettingsUpdate(version=2, values=AccountPoolSettings(default_concurrency_limit=3))
    assert merge_runtime_settings(current, patch, "common").values.auth_file_template == current.values.auth_file_template


def test_common_greetings_roundtrip_preserves_other_sections_and_older_clients():
    from account_pool.settings import AccountPoolSettings as ManagerSettings
    current = AccountPoolSettingsView(version=2, values=AccountPoolSettings(default_proxy_profile_id="keep"))
    patch = AccountPoolSettingsUpdate(version=2, values=AccountPoolSettings(model_test_greetings=["Hello", "你好"]))
    merged = merge_runtime_settings(current, patch, "common")
    assert ManagerSettings.model_validate_json(merged.values.model_dump_json()).model_test_greetings == ("Hello", "你好")
    assert merged.values.default_proxy_profile_id == "keep"
    saved = AccountPoolSettingsView(version=3, values=merged.values)
    older_client = AccountPoolSettingsUpdate(version=3, values=AccountPoolSettings(default_concurrency_limit=2))
    assert merge_runtime_settings(saved, older_client, "common").values.model_test_greetings == ("Hello", "你好")
    cleared = older_client.model_copy(update={"values": AccountPoolSettings(model_test_greetings=[])})
    assert merge_runtime_settings(saved, cleared, "common").values.model_test_greetings == ()


@pytest.mark.parametrize("interval", [None, 0, 15, 300])
def test_heartbeat_runtime_save_and_roundtrip(interval):
    from account_pool.settings import AccountPoolSettings as ManagerSettings

    current = AccountPoolSettingsView(version=2, values=AccountPoolSettings(default_proxy_profile_id="keep"))
    patch = AccountPoolSettingsUpdate(version=2, values=AccountPoolSettings(sse_heartbeat_interval_seconds=interval))
    merged = merge_runtime_settings(current, patch, "streaming")
    restored = ManagerSettings.model_validate_json(merged.values.model_dump_json())
    assert restored.sse_heartbeat_interval_seconds == interval
    assert restored.default_proxy_profile_id == "keep"


@pytest.mark.parametrize("interval", [-1, 301, float("nan"), float("inf")])
def test_heartbeat_rejects_invalid_interval(interval):
    from account_pool.settings import AccountPoolSettings as ManagerSettings
    from pydantic import ValidationError

    for model in (AccountPoolSettings, ManagerSettings):
        with pytest.raises(ValidationError):
            model(sse_heartbeat_interval_seconds=interval)


@pytest.mark.parametrize("admin", [False, True])
def test_native_runtime_api_requires_admin_and_writes_only_selected_module(admin):
    calls = []
    original = AccountPoolSettingsView(version=2, values=AccountPoolSettings(default_proxy_profile_id="keep"))

    async def request_manager(method, path, body):
        calls.append((method, path, body))
        if method == "GET":
            return httpx.Response(200, content=original.model_dump_json())
        saved = AccountPoolSettingsUpdate.model_validate_json(body)
        return httpx.Response(200, content=AccountPoolSettingsView(version=3, values=saved.values).model_dump_json())

    def require_admin(auth):
        if auth.user_role != "proxy_admin":
            raise HTTPException(403)

    app = FastAPI()
    app.include_router(create_runtime_configuration_router(request_manager, require_admin))
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(
        user_role="proxy_admin" if admin else "internal_user"
    )
    with TestClient(app) as client:
        response = client.put(
            "/config/runtime/streaming",
            json={"version": 2, "values": {"streaming_enabled": False}},
        )
    assert response.status_code == (200 if admin else 403)
    if not admin:
        assert calls == []
        return
    assert response.json()["values"]["default_proxy_profile_id"] == "keep"
    assert response.json()["values"]["streaming_enabled"] is False
    assert [(method, path) for method, path, _ in calls] == [("GET", "/api/settings"), ("PUT", "/api/settings")]


def test_policy_section_updates_routing_defaults_and_keeps_other_sections() -> None:
    current: AccountPoolSettingsView = AccountPoolSettingsView(
        version=3,
        values=AccountPoolSettings(default_concurrency_limit=5, quota_switch_project=True),
    )
    patch: AccountPoolSettingsUpdate = AccountPoolSettingsUpdate(
        version=3,
        values=AccountPoolSettings(
            default_route="quota",
            max_attempts=4,
            quota_reserve_percent=75,
            quota_snapshot_max_age=1800,
        ),
    )

    merged: AccountPoolSettingsUpdate = merge_runtime_settings(current, patch, "policy")

    assert merged.values.default_route == "quota"
    assert merged.values.max_attempts == 4
    assert merged.values.quota_reserve_percent == 75
    assert merged.values.quota_snapshot_max_age == 1800
    assert merged.values.default_concurrency_limit == 5
    assert merged.values.quota_switch_project is True


def test_policy_section_rejects_stale_versions() -> None:
    current: AccountPoolSettingsView = AccountPoolSettingsView(version=3, values=AccountPoolSettings())
    patch: AccountPoolSettingsUpdate = AccountPoolSettingsUpdate(
        version=2, values=AccountPoolSettings(quota_reserve_percent=75)
    )

    with pytest.raises(HTTPException) as error:
        merge_runtime_settings(current, patch, "policy")

    assert error.value.status_code == 409
