"""把供应商运行参数接入原生配置入口，按模块保存并保留其他模块的配置。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Final, Literal

from fastapi import APIRouter, Depends, HTTPException

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_management import ManagementRequest
from litellm.proxy.management_endpoints.account_pool_management_models import (
    AccountPoolSettings,
    AccountPoolSettingsUpdate,
    AccountPoolSettingsView,
)

RuntimeSection = Literal["common", "access", "network", "quota", "streaming", "advanced", "payload"]
_FIELDS: Final = {
    "common": ("default_concurrency_limit", "default_model_discovery", "common_profiles"),
    "access": ("oauth_excluded_models", "oauth_model_aliases", "oauth_request_scoped_errors", "access_profiles"),
    "network": (
        "default_proxy_profile_id",
        "max_attempts",
        "request_timeout_seconds",
        "websocket_enabled",
        "network_profiles",
    ),
    "quota": ("quota_switch_project", "quota_switch_preview_model", "quota_profiles"),
    "streaming": ("streaming_enabled", "streaming_profiles", "streaming_rules"),
    "advanced": ("plugins_enabled", "websocket_auth_enabled", "force_model_prefix", "advanced_profiles"),
    "payload": ("payload", "payload_profiles"),
}


def merge_runtime_settings(
    current: AccountPoolSettingsView, patch: AccountPoolSettingsUpdate, section: RuntimeSection
) -> AccountPoolSettingsUpdate:
    if current.version != patch.version:
        raise HTTPException(409, "Configuration changed; refresh before saving")
    updates: Final = {field: getattr(patch.values, field) for field in _FIELDS[section]}
    return AccountPoolSettingsUpdate(
        version=current.version,
        values=AccountPoolSettings.model_validate({**current.values.model_dump(), **updates}),
    )


def create_runtime_configuration_router(
    request_manager: ManagementRequest, require_admin: Callable[[UserAPIKeyAuth], None]
) -> APIRouter:
    def authorize(user: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)]) -> None:
        require_admin(user)

    router: Final = APIRouter(prefix="/config/runtime", tags=["config"], dependencies=[Depends(authorize)])

    @router.get("", response_model=AccountPoolSettingsView)
    async def read_runtime_configuration() -> AccountPoolSettingsView:
        response: Final = await request_manager("GET", "/api/settings", None)
        if response.is_error:
            raise HTTPException(response.status_code, "Runtime configuration is unavailable")
        return AccountPoolSettingsView.model_validate_json(response.content)

    async def update_runtime_configuration(
        section: RuntimeSection, patch: AccountPoolSettingsUpdate
    ) -> AccountPoolSettingsView:
        current: Final = await read_runtime_configuration()
        merged: Final = merge_runtime_settings(current, patch, section)
        response: Final = await request_manager("PUT", "/api/settings", merged.model_dump_json().encode())
        if response.is_error:
            raise HTTPException(response.status_code, "Runtime configuration was not saved; refresh and retry")
        return AccountPoolSettingsView.model_validate_json(response.content)

    router.add_api_route(
        "/{section}", update_runtime_configuration, methods=["PUT"], response_model=AccountPoolSettingsView
    )
    return router
