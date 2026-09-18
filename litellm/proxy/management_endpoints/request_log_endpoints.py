from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Final, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_full_log_api import create_full_log_router
from litellm.proxy.management_endpoints.account_pool_management import ManagementRequest, parse_response
from litellm.proxy.management_endpoints.account_pool_management_models import (
    AccountPoolLogClearResult,
    AccountPoolLogStorageStats,
    AccountPoolSettingsUpdate,
    AccountPoolSettingsView,
    ErrorLogDetail,
    ErrorLogPage,
    ErrorLogQuery,
    ErrorStats,
)


class RequestLogSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    full_logging_enabled: bool = False
    full_log_skip_failed: bool = False
    daily_log_retention_days: int = Field(default=30, ge=1, le=3650)
    full_log_retention_days: int = Field(default=30, ge=1, le=3650)
    file_logging_enabled: bool = False
    debug_logging_enabled: bool = False
    request_log_enabled: bool = False
    usage_statistics_enabled: bool = False
    logs_max_total_size_mb: int = Field(default=0, ge=0, le=100000)
    error_logs_max_files: int = Field(default=10, ge=0, le=10000)


class RequestLogSettingsView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=0)
    values: RequestLogSettings
    requires_reload: bool = False


def create_request_log_router(
    request_manager: ManagementRequest,
    require_admin: Callable[[UserAPIKeyAuth], None],
) -> APIRouter:
    def authorize(user: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)], response: Response) -> None:
        require_admin(user)
        response.headers["Cache-Control"] = "no-store"

    router: Final = APIRouter(prefix="/logs", tags=["Logs"], dependencies=[Depends(authorize)])
    router.include_router(create_full_log_router(prefix="/full"))

    async def call(
        method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"], path: str, body: bytes | None = None
    ) -> bytes:
        response: Final = await request_manager(method, path, body)
        if response.is_error:
            raise HTTPException(response.status_code, "Log operation failed; refresh and retry")
        return response.content

    async def settings_view() -> AccountPoolSettingsView:
        return parse_response(await call("GET", "/api/settings"), TypeAdapter(AccountPoolSettingsView))

    async def get_settings(response: Response) -> RequestLogSettingsView:
        response.headers["Cache-Control"] = "no-store"
        current: Final = await settings_view()
        return RequestLogSettingsView(
            version=current.version,
            values=RequestLogSettings.model_validate(
                current.values.model_dump(include=set(RequestLogSettings.model_fields))
            ),
            requires_reload=current.requires_reload,
        )

    async def update_settings(request: RequestLogSettingsView, response: Response) -> RequestLogSettingsView:
        response.headers["Cache-Control"] = "no-store"
        current: Final = await settings_view()
        if current.version != request.version:
            raise HTTPException(409, "Settings changed; refresh before saving")
        merged: Final = AccountPoolSettingsUpdate(
            version=current.version,
            values=type(current.values).model_validate({**current.values.model_dump(), **request.values.model_dump()}),
        )
        saved: Final = parse_response(
            await call("PUT", "/api/settings", merged.model_dump_json().encode()), TypeAdapter(AccountPoolSettingsView)
        )
        return RequestLogSettingsView(
            version=saved.version,
            values=RequestLogSettings.model_validate(
                saved.values.model_dump(include=set(RequestLogSettings.model_fields))
            ),
            requires_reload=saved.requires_reload,
        )

    async def logs(query: Annotated[ErrorLogQuery, Query()]) -> ErrorLogPage:
        params: Final = httpx.QueryParams(query.model_dump(mode="json", exclude_none=True))
        return parse_response(await call("GET", f"/api/logs?{params}"), TypeAdapter(ErrorLogPage))

    async def export_logs(query: Annotated[ErrorLogQuery, Query()]) -> PlainTextResponse:
        params: Final = httpx.QueryParams(query.model_dump(mode="json", exclude_none=True))
        payload: Final = await call("GET", f"/api/logs/export?{params}")
        return PlainTextResponse(
            payload,
            media_type="application/x-ndjson",
            headers={"Content-Disposition": "attachment; filename=operation-logs.ndjson", "Cache-Control": "no-store"},
        )

    async def clear_logs(older_than_days: Literal["7", "14", "30", "45"] | None = None) -> AccountPoolLogClearResult:
        params: Final = "" if older_than_days is None else f"?older_than_days={older_than_days}"
        return parse_response(await call("DELETE", f"/api/logs{params}"), TypeAdapter(AccountPoolLogClearResult))

    async def log_storage() -> AccountPoolLogStorageStats:
        return parse_response(await call("GET", "/api/logs/storage"), TypeAdapter(AccountPoolLogStorageStats))

    async def stats(query: Annotated[ErrorLogQuery, Query()]) -> ErrorStats:
        params: Final = httpx.QueryParams(query.model_dump(mode="json", exclude_none=True, exclude={"limit", "offset"}))
        path: Final = "/api/stats" if not params else f"/api/stats?{params}"
        return parse_response(await call("GET", path), TypeAdapter(ErrorStats))

    async def log_detail(event_id: UUID) -> ErrorLogDetail:
        return parse_response(await call("GET", f"/api/logs/{event_id}"), TypeAdapter(ErrorLogDetail))

    router.add_api_route("/settings", get_settings, methods=["GET"])
    router.add_api_route("/settings", update_settings, methods=["PUT"])
    router.add_api_route("/operations", logs, methods=["GET"])
    router.add_api_route("/operations/export", export_logs, methods=["GET"], response_class=PlainTextResponse)
    router.add_api_route("/operations", clear_logs, methods=["DELETE"])
    router.add_api_route("/operations/storage", log_storage, methods=["GET"])
    router.add_api_route("/operations/stats", stats, methods=["GET"])
    router.add_api_route("/operations/{event_id}", log_detail, methods=["GET"])
    return router
