"""本模块代理卡片凭据、策略和日志接口，复用统一传输及管理员权限检查。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Final, Literal, TypeVar
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse
from pydantic import TypeAdapter

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_management_models import (
    AccountPoolCredential,
    AccountPoolCredentialDeleteRequest,
    AccountPoolCredentialMutationResult,
    AccountPoolCredentialRequest,
    AccountPoolLogClearResult,
    AccountPoolPluginManifest,
    AccountPoolPluginRecord,
    AccountPoolSettingsHistoryEntry,
    AccountPoolSettingsPreview,
    AccountPoolSettingsRollbackRequest,
    AccountPoolSettingsUpdate,
    AccountPoolSettingsView,
    BatchJob,
    BatchRequest,
    CardKeyChange,
    CardKeyIssue,
    CardKeyStatus,
    ErrorLogDetail,
    ErrorLogPage,
    ErrorLogQuery,
    ErrorStats,
    PolicyUpdate,
    PolicyView,
)

ManagementRequest = Callable[[Literal["GET", "POST", "PUT", "DELETE"], str, bytes | None], Awaitable[httpx.Response]]


def create_management_router(
    request_manager: ManagementRequest,
    require_admin: Callable[[UserAPIKeyAuth], None],
) -> APIRouter:
    def authorize(user: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)]) -> None:
        require_admin(user)

    router: Final = APIRouter(dependencies=[Depends(authorize)])

    async def call(method: Literal["GET", "POST", "PUT", "DELETE"], path: str, body: bytes | None = None) -> bytes:
        response: Final = await request_manager(method, path, body)
        if response.is_error:
            raise HTTPException(response.status_code, "Account pool operation failed; refresh and retry")
        return response.content

    @router.get("/cards/{card_id}/key/status")
    async def key_status(card_id: UUID, response: Response) -> CardKeyStatus | None:
        response.headers["Cache-Control"] = "no-store"
        return parse_response(await call("GET", f"/api/cards/{card_id}/key/status"), TypeAdapter(CardKeyStatus | None))

    @router.post("/cards/{card_id}/key")
    async def create_key(card_id: UUID, response: Response) -> CardKeyIssue:
        response.headers["Cache-Control"] = "no-store"
        return parse_response(await call("POST", f"/api/cards/{card_id}/key"), TypeAdapter(CardKeyIssue))

    @router.post("/cards/{card_id}/key/rotate")
    async def rotate_key(card_id: UUID, request: CardKeyChange, response: Response) -> CardKeyIssue:
        response.headers["Cache-Control"] = "no-store"
        return parse_response(
            await call("POST", f"/api/cards/{card_id}/key/rotate", request.model_dump_json().encode()),
            TypeAdapter(CardKeyIssue),
        )

    @router.delete("/cards/{card_id}/key", status_code=204)
    async def revoke_key(card_id: UUID, request: CardKeyChange) -> None:
        await call("DELETE", f"/api/cards/{card_id}/key", request.model_dump_json().encode())

    @router.get("/logs")
    async def logs(query: Annotated[ErrorLogQuery, Query()]) -> ErrorLogPage:
        params: Final = httpx.QueryParams(query.model_dump(mode="json", exclude_none=True))
        return parse_response(await call("GET", f"/api/logs?{params}"), TypeAdapter(ErrorLogPage))

    @router.get("/logs/export", response_class=PlainTextResponse)
    async def export_logs(query: Annotated[ErrorLogQuery, Query()]) -> PlainTextResponse:
        params: Final = httpx.QueryParams(query.model_dump(mode="json", exclude_none=True))
        payload: Final = await call("GET", f"/api/logs/export?{params}")
        return PlainTextResponse(
            payload,
            media_type="application/x-ndjson",
            headers={"Content-Disposition": "attachment; filename=account-pool-logs.ndjson"},
        )

    @router.delete("/logs", response_model=AccountPoolLogClearResult)
    async def clear_logs() -> AccountPoolLogClearResult:
        return parse_response(await call("DELETE", "/api/logs"), TypeAdapter(AccountPoolLogClearResult))

    @router.get("/credentials", response_model=tuple[AccountPoolCredential, ...])
    async def credentials() -> tuple[AccountPoolCredential, ...]:
        return parse_response(await call("GET", "/api/credentials"), TypeAdapter(tuple[AccountPoolCredential, ...]))

    @router.post("/environments/{card_id}/credentials")
    async def add_credential(card_id: UUID, request: AccountPoolCredentialRequest) -> AccountPoolCredentialMutationResult:
        return parse_response(
            await call("POST", f"/api/environments/{card_id}/credentials", request.model_dump_json().encode()),
            TypeAdapter(AccountPoolCredentialMutationResult),
        )

    @router.delete("/environments/{card_id}/credentials")
    async def delete_credential(card_id: UUID, request: AccountPoolCredentialDeleteRequest) -> AccountPoolCredentialMutationResult:
        return parse_response(
            await call("DELETE", f"/api/environments/{card_id}/credentials", request.model_dump_json().encode()),
            TypeAdapter(AccountPoolCredentialMutationResult),
        )

    @router.get("/plugins", response_model=tuple[AccountPoolPluginRecord, ...])
    async def plugins() -> tuple[AccountPoolPluginRecord, ...]:
        return parse_response(await call("GET", "/api/plugins"), TypeAdapter(tuple[AccountPoolPluginRecord, ...]))

    @router.get("/plugin-store", response_model=tuple[AccountPoolPluginManifest, ...])
    async def plugin_store() -> tuple[AccountPoolPluginManifest, ...]:
        return parse_response(await call("GET", "/api/plugin-store"), TypeAdapter(tuple[AccountPoolPluginManifest, ...]))

    @router.post("/plugins", response_model=AccountPoolPluginRecord)
    async def install_plugin(manifest: AccountPoolPluginManifest) -> AccountPoolPluginRecord:
        return parse_response(
            await call("POST", "/api/plugins", manifest.model_dump_json().encode()),
            TypeAdapter(AccountPoolPluginRecord),
        )

    @router.post("/plugins/{plugin_id}/enable", response_model=AccountPoolPluginRecord)
    async def enable_plugin(plugin_id: str) -> AccountPoolPluginRecord:
        return parse_response(await call("POST", f"/api/plugins/{plugin_id}/enable"), TypeAdapter(AccountPoolPluginRecord))

    @router.post("/plugins/{plugin_id}/disable", response_model=AccountPoolPluginRecord)
    async def disable_plugin(plugin_id: str) -> AccountPoolPluginRecord:
        return parse_response(await call("POST", f"/api/plugins/{plugin_id}/disable"), TypeAdapter(AccountPoolPluginRecord))

    @router.delete("/plugins/{plugin_id}", status_code=204)
    async def uninstall_plugin(plugin_id: str) -> None:
        await call("DELETE", f"/api/plugins/{plugin_id}")

    @router.get("/logs/{event_id}")
    async def log_detail(event_id: UUID) -> ErrorLogDetail:
        return parse_response(await call("GET", f"/api/logs/{event_id}"), TypeAdapter(ErrorLogDetail))

    @router.get("/environments/{card_id}/policy")
    async def get_policy(card_id: UUID) -> PolicyView:
        return parse_response(await call("GET", f"/api/environments/{card_id}/policy"), TypeAdapter(PolicyView))

    @router.get("/policies")
    async def list_policies() -> tuple[PolicyView, ...]:
        return parse_response(await call("GET", "/api/policies"), TypeAdapter(tuple[PolicyView, ...]))

    @router.put("/environments/{card_id}/policy")
    async def save_policy(card_id: UUID, request: PolicyUpdate) -> PolicyView:
        return parse_response(
            await call("PUT", f"/api/environments/{card_id}/policy", request.model_dump_json().encode()),
            TypeAdapter(PolicyView),
        )

    @router.get("/settings")
    async def get_settings() -> AccountPoolSettingsView:
        return parse_response(await call("GET", "/api/settings"), TypeAdapter(AccountPoolSettingsView))

    @router.get("/settings/history")
    async def settings_history() -> tuple[AccountPoolSettingsHistoryEntry, ...]:
        return parse_response(
            await call("GET", "/api/settings/history"), TypeAdapter(tuple[AccountPoolSettingsHistoryEntry, ...])
        )

    @router.put("/settings")
    async def update_settings(request: AccountPoolSettingsUpdate) -> AccountPoolSettingsView:
        return parse_response(
            await call("PUT", "/api/settings", request.model_dump_json().encode()), TypeAdapter(AccountPoolSettingsView)
        )

    @router.post("/settings/preview")
    async def preview_settings(request: AccountPoolSettingsUpdate) -> AccountPoolSettingsPreview:
        return parse_response(
            await call("POST", "/api/settings/preview", request.model_dump_json().encode()),
            TypeAdapter(AccountPoolSettingsPreview),
        )

    @router.post("/settings/rollback")
    async def rollback_settings(request: AccountPoolSettingsRollbackRequest) -> AccountPoolSettingsView:
        return parse_response(
            await call("POST", "/api/settings/rollback", request.model_dump_json().encode()),
            TypeAdapter(AccountPoolSettingsView),
        )

    @router.post("/batches", response_model=BatchJob, status_code=202)
    async def submit_batch(request: BatchRequest, response: Response) -> BatchJob:
        response.headers["Cache-Control"] = "no-store"
        return parse_response(
            await call("POST", "/api/batches", request.model_dump_json().encode()), TypeAdapter(BatchJob)
        )

    @router.get("/batches", response_model=tuple[BatchJob, ...])
    async def list_batches(response: Response) -> tuple[BatchJob, ...]:
        response.headers["Cache-Control"] = "no-store"
        return parse_response(await call("GET", "/api/batches"), TypeAdapter(tuple[BatchJob, ...]))

    @router.get("/batches/{job_id}", response_model=BatchJob)
    async def get_batch(job_id: UUID, response: Response) -> BatchJob:
        response.headers["Cache-Control"] = "no-store"
        return parse_response(await call("GET", f"/api/batches/{job_id}"), TypeAdapter(BatchJob))

    @router.get("/stats", response_model=ErrorStats)
    async def stats(
        card_id: UUID | None = None,
        account_id: UUID | None = None,
        model: str | None = None,
    ) -> ErrorStats:
        params: Final = httpx.QueryParams(
            {
                key: str(value)
                for key, value in (("card_id", card_id), ("account_id", account_id), ("model", model))
                if value is not None
            }
        )
        path: Final = "/api/stats" if not params else f"/api/stats?{params}"
        return parse_response(await call("GET", path), TypeAdapter(ErrorStats))

    return router


T = TypeVar("T")


def parse_response(payload: bytes, adapter: TypeAdapter[T]) -> T:
    try:
        return adapter.validate_json(payload)
    except ValueError as error:
        raise HTTPException(502, "Account Pool Manager returned an invalid response") from error
