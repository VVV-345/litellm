"""本模块代理卡片凭据、策略和日志接口，复用统一传输及管理员权限检查。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Final, Literal, TypeVar
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import TypeAdapter

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_management_models import (
    AccountPoolCredential,
    AccountPoolCredentialDeleteRequest,
    AccountPoolCredentialMutationResult,
    AccountPoolCredentialRequest,
    AccountPoolPluginManifest,
    AccountPoolPluginRecord,
    AccountPoolSettingsHistoryEntry,
    AccountPoolSettingsPreview,
    AccountPoolSettingsRollbackRequest,
    AccountPoolSettingsUpdate,
    AccountPoolSettingsView,
    BatchJob,
    BatchRequest,
    CodexReviewPackage,
    PolicyUpdate,
    PolicyView,
    UpstreamSyncDispatch,
    UpstreamSyncView,
)

ManagementRequest = Callable[
    [Literal["GET", "POST", "PUT", "DELETE", "PATCH"], str, bytes | None], Awaitable[httpx.Response]
]


def create_management_router(
    request_manager: ManagementRequest,
    require_admin: Callable[[UserAPIKeyAuth], None],
) -> APIRouter:
    def authorize(user: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)]) -> None:
        require_admin(user)

    router: Final = APIRouter(dependencies=[Depends(authorize)])

    async def call(
        method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"], path: str, body: bytes | None = None
    ) -> bytes:
        response: Final = await request_manager(method, path, body)
        if response.is_error:
            raise HTTPException(response.status_code, "Account pool operation failed; refresh and retry")
        return response.content

    @router.get("/credentials", response_model=tuple[AccountPoolCredential, ...])
    @router.get("/auth-files", response_model=tuple[AccountPoolCredential, ...])
    async def credentials() -> tuple[AccountPoolCredential, ...]:
        return parse_response(await call("GET", "/api/auth-files"), TypeAdapter(tuple[AccountPoolCredential, ...]))

    @router.post("/environments/{card_id}/credentials")
    async def add_credential(
        card_id: UUID, request: AccountPoolCredentialRequest
    ) -> AccountPoolCredentialMutationResult:
        return parse_response(
            await call("POST", f"/api/environments/{card_id}/credentials", request.model_dump_json().encode()),
            TypeAdapter(AccountPoolCredentialMutationResult),
        )

    @router.delete("/environments/{card_id}/credentials")
    async def delete_credential(
        card_id: UUID, request: AccountPoolCredentialDeleteRequest
    ) -> AccountPoolCredentialMutationResult:
        return parse_response(
            await call("DELETE", f"/api/environments/{card_id}/credentials", request.model_dump_json().encode()),
            TypeAdapter(AccountPoolCredentialMutationResult),
        )

    @router.get("/plugins", response_model=tuple[AccountPoolPluginRecord, ...])
    async def plugins() -> tuple[AccountPoolPluginRecord, ...]:
        return parse_response(await call("GET", "/api/plugins"), TypeAdapter(tuple[AccountPoolPluginRecord, ...]))

    @router.get("/plugin-store", response_model=tuple[AccountPoolPluginManifest, ...])
    async def plugin_store() -> tuple[AccountPoolPluginManifest, ...]:
        return parse_response(
            await call("GET", "/api/plugin-store"), TypeAdapter(tuple[AccountPoolPluginManifest, ...])
        )

    @router.post("/plugins", response_model=AccountPoolPluginRecord)
    async def install_plugin(manifest: AccountPoolPluginManifest) -> AccountPoolPluginRecord:
        return parse_response(
            await call("POST", "/api/plugins", manifest.model_dump_json().encode()),
            TypeAdapter(AccountPoolPluginRecord),
        )

    @router.post("/plugins/{plugin_id}/enable", response_model=AccountPoolPluginRecord)
    async def enable_plugin(plugin_id: str) -> AccountPoolPluginRecord:
        return parse_response(
            await call("POST", f"/api/plugins/{plugin_id}/enable"), TypeAdapter(AccountPoolPluginRecord)
        )

    @router.post("/plugins/{plugin_id}/disable", response_model=AccountPoolPluginRecord)
    async def disable_plugin(plugin_id: str) -> AccountPoolPluginRecord:
        return parse_response(
            await call("POST", f"/api/plugins/{plugin_id}/disable"), TypeAdapter(AccountPoolPluginRecord)
        )

    @router.delete("/plugins/{plugin_id}", status_code=204)
    async def uninstall_plugin(plugin_id: str) -> None:
        await call("DELETE", f"/api/plugins/{plugin_id}")

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

    @router.get("/settings/history")
    async def settings_history() -> tuple[AccountPoolSettingsHistoryEntry, ...]:
        return parse_response(
            await call("GET", "/api/settings/history"), TypeAdapter(tuple[AccountPoolSettingsHistoryEntry, ...])
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

    @router.get("/upstream-sync", response_model=UpstreamSyncView)
    async def upstream_sync_status(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        response: Response,
    ) -> UpstreamSyncView:
        response.headers["Cache-Control"] = "no-store"
        return parse_response(await call("GET", "/api/upstream-sync"), TypeAdapter(UpstreamSyncView))

    @router.post("/upstream-sync/analyze", response_model=UpstreamSyncDispatch, status_code=202)
    async def analyze_upstream(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        response: Response,
    ) -> UpstreamSyncDispatch:
        response.headers["Cache-Control"] = "no-store"
        return parse_response(
            await call("POST", "/api/upstream-sync/analyze"),
            TypeAdapter(UpstreamSyncDispatch),
        )

    @router.post("/upstream-sync/promote", response_model=UpstreamSyncDispatch, status_code=202)
    async def promote_upstream(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        response: Response,
    ) -> UpstreamSyncDispatch:
        response.headers["Cache-Control"] = "no-store"
        return parse_response(
            await call("POST", "/api/upstream-sync/promote"),
            TypeAdapter(UpstreamSyncDispatch),
        )

    @router.get("/upstream-sync/codex-review", response_model=CodexReviewPackage)
    async def codex_review_package(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        response: Response,
    ) -> CodexReviewPackage:
        response.headers["Cache-Control"] = "no-store"
        return parse_response(
            await call("GET", "/api/upstream-sync/codex-review"),
            TypeAdapter(CodexReviewPackage),
        )

    return router


T = TypeVar("T")


def parse_response(payload: bytes, adapter: TypeAdapter[T]) -> T:
    try:
        return adapter.validate_json(payload)
    except ValueError as error:
        raise HTTPException(502, "Account Pool Manager returned an invalid response") from error
