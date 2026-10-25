"""本模块代理卡片凭据、策略和日志接口，复用统一传输及管理员权限检查。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Final, Literal, TypeVar
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import TypeAdapter, ValidationError

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
    CockpitPolicyApply,
    CockpitPolicyRuntime,
    CockpitPolicyUpdate,
    CockpitPolicyView,
    CockpitScopeActivationRequest,
    CockpitScopePublishRequest,
    CockpitScopeRevokeRequest,
    CockpitSubjectScopeView,
    CodexReviewPackage,
    PolicyUpdate,
    PolicyView,
    UpstreamSyncDispatch,
    UpstreamSyncView,
)

ManagementRequest = Callable[
    [Literal["GET", "POST", "PUT", "DELETE", "PATCH"], str, bytes | None], Awaitable[httpx.Response]
]


def _upstream_sync_query(request_id: UUID | None, refresh: bool) -> str:
    query: Final = "&".join(
        value
        for value in (
            f"request_id={request_id}" if request_id is not None else "",
            "refresh=true" if refresh else "",
        )
        if value
    )
    return f"?{query}" if query else ""


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
            if response.status_code == 429 and path.startswith("/api/upstream-sync"):
                raise HTTPException(
                    429, "GitHub API rate limit exceeded; retry later or configure this target's GitHub token"
                )
            raise HTTPException(response.status_code, "Account pool operation failed; refresh and retry")
        return response.content

    async def cockpit_policy_call(method: Literal["GET", "PUT"], body: bytes | None = None) -> CockpitPolicyView:
        try:
            upstream: Final = await request_manager(method, "/api/cockpit/policy", body)
        except HTTPException:
            raise HTTPException(
                503,
                "write_outcome_unknown" if method == "PUT" else "unavailable",
                headers={"Cache-Control": "no-store"},
            ) from None
        if upstream.status_code != 200:
            safe_codes: Final = {
                409: frozenset({"revision_conflict"}),
                422: frozenset({"unsupported_account_or_rule", "unsupported_strategy", "invalid_rule_accounts"}),
                503: frozenset({"unavailable", "write_outcome_unknown"}),
            }
            try:
                error: Final = TypeAdapter(dict[str, str]).validate_json(upstream.content)
            except ValidationError:
                raise HTTPException(
                    503 if method == "PUT" else 502,
                    "write_outcome_unknown" if method == "PUT" else "unavailable",
                    headers={"Cache-Control": "no-store"},
                ) from None
            detail: Final = error.get("detail", "")
            accepted: Final = detail in safe_codes.get(upstream.status_code, frozenset())
            raise HTTPException(
                upstream.status_code if accepted else 503 if method == "PUT" else 502,
                detail if accepted else "write_outcome_unknown" if method == "PUT" else "unavailable",
                headers={"Cache-Control": "no-store"},
            )
        try:
            return CockpitPolicyView.model_validate_json(upstream.content)
        except ValidationError:
            raise HTTPException(
                503 if method == "PUT" else 502,
                "write_outcome_unknown" if method == "PUT" else "invalid_response",
                headers={"Cache-Control": "no-store"},
            ) from None

    @router.get("/cockpit/policy")
    async def get_cockpit_policy(response: Response) -> CockpitPolicyView:
        response.headers["Cache-Control"] = "no-store"
        return await cockpit_policy_call("GET")

    @router.put("/cockpit/policy")
    async def put_cockpit_policy(request: CockpitPolicyUpdate, response: Response) -> CockpitPolicyView:
        response.headers["Cache-Control"] = "no-store"
        return await cockpit_policy_call("PUT", request.model_dump_json().encode())

    async def cockpit_runtime_call(request: CockpitPolicyApply | None = None) -> CockpitPolicyRuntime:
        failure: Final = "apply_outcome_unknown" if request is not None else "unavailable"
        status: Final = 503 if request is not None else 502
        headers: Final = {"Cache-Control": "no-store"}
        try:
            upstream: Final = await request_manager(
                "POST" if request is not None else "GET",
                "/api/cockpit/policy/apply" if request is not None else "/api/cockpit/policy/runtime",
                request.model_dump_json().encode() if request is not None else None,
            )
        except HTTPException:
            raise HTTPException(503, failure, headers=headers) from None
        if upstream.status_code != 200:
            safe_codes: Final = {
                409: frozenset({"revision_conflict"}),
                422: frozenset({"non_policy_change", "unsupported_policy"}),
                503: frozenset({"unavailable", "apply_outcome_unknown"}),
            }
            try:
                error: Final = TypeAdapter(dict[str, str]).validate_json(upstream.content)
            except ValidationError:
                raise HTTPException(status, failure, headers=headers) from None
            detail: Final = error.get("detail", "")
            accepted: Final = detail in safe_codes.get(upstream.status_code, frozenset())
            raise HTTPException(
                upstream.status_code if accepted else status, detail if accepted else failure, headers=headers
            )
        try:
            result: Final = CockpitPolicyRuntime.model_validate_json(upstream.content)
        except ValidationError:
            raise HTTPException(status, failure, headers=headers) from None
        if request is not None and (
            result.active_revision != request.revision
            or result.persisted_revision != request.revision
            or not result.apply_supported
        ):
            raise HTTPException(503, "apply_outcome_unknown", headers=headers)
        return result

    @router.get("/cockpit/policy/runtime")
    async def get_cockpit_runtime(response: Response) -> CockpitPolicyRuntime:
        response.headers["Cache-Control"] = "no-store"
        return await cockpit_runtime_call()

    @router.post("/cockpit/policy/apply")
    async def post_cockpit_apply(request: CockpitPolicyApply, response: Response) -> CockpitPolicyRuntime:
        response.headers["Cache-Control"] = "no-store"
        return await cockpit_runtime_call(request)

    async def cockpit_scope_call(
        method: Literal["GET", "POST", "DELETE"],
        path: str,
        body: bytes | None = None,
    ) -> CockpitSubjectScopeView:
        headers: Final = {"Cache-Control": "no-store"}
        try:
            upstream: Final = await request_manager(method, path, body)
        except HTTPException:
            raise HTTPException(
                503,
                "write_outcome_unknown" if method != "GET" else "unavailable",
                headers=headers,
            ) from None
        if upstream.status_code != 200:
            safe_codes: Final = {
                409: frozenset(
                    {
                        "activation_blocked",
                        "manifest_changed",
                        "not_published",
                        "publication_conflict",
                        "receipt_stale",
                        "revision_conflict",
                        "subject_unverified",
                    }
                ),
                422: frozenset(
                    {
                        "account_missing",
                        "invalid_manifest",
                        "invalid_receipt",
                        "model_scope_unsupported",
                        "model_unmapped",
                        "scope_unverified",
                    }
                ),
                503: frozenset({"unavailable", "write_outcome_unknown"}),
            }
            try:
                error: Final = TypeAdapter(dict[str, str]).validate_json(upstream.content)
            except ValidationError:
                raise HTTPException(502, "invalid_response", headers=headers) from None
            detail: Final = error.get("detail", "")
            accepted: Final = detail in safe_codes.get(upstream.status_code, frozenset())
            raise HTTPException(
                upstream.status_code if accepted else (503 if method != "GET" else 502),
                detail if accepted else ("write_outcome_unknown" if method != "GET" else "invalid_response"),
                headers=headers,
            )
        try:
            return CockpitSubjectScopeView.model_validate_json(upstream.content)
        except ValidationError:
            raise HTTPException(502, "invalid_response", headers=headers) from None

    def scope_mismatch(expected: str, view: CockpitSubjectScopeView) -> None:
        if view.subject_hash != expected:
            raise HTTPException(502, "invalid_response", headers={"Cache-Control": "no-store"})

    @router.get("/cockpit/scope/{subject_hash}")
    async def get_cockpit_scope(  # pyright: ignore[reportUnusedFunction]  # FastAPI registers by decorator.
        subject_hash: str,
        response: Response,
    ) -> CockpitSubjectScopeView:
        response.headers["Cache-Control"] = "no-store"
        view: Final = await cockpit_scope_call("GET", f"/api/cockpit/scope/{subject_hash}")
        scope_mismatch(subject_hash, view)
        return view

    @router.post("/cockpit/scope")
    async def publish_cockpit_scope(  # pyright: ignore[reportUnusedFunction]  # FastAPI registers by decorator.
        request: CockpitScopePublishRequest,
        response: Response,
    ) -> CockpitSubjectScopeView:
        response.headers["Cache-Control"] = "no-store"
        view: Final = await cockpit_scope_call("POST", "/api/cockpit/scope", request.model_dump_json().encode())
        scope_mismatch(request.subject_hash, view)
        return view

    @router.delete("/cockpit/scope")
    async def revoke_cockpit_scope(  # pyright: ignore[reportUnusedFunction]  # FastAPI registers by decorator.
        request: CockpitScopeRevokeRequest,
        response: Response,
    ) -> CockpitSubjectScopeView:
        response.headers["Cache-Control"] = "no-store"
        view: Final = await cockpit_scope_call("DELETE", "/api/cockpit/scope", request.model_dump_json().encode())
        scope_mismatch(request.subject_hash, view)
        return view

    @router.post("/cockpit/scope/activate")
    async def activate_cockpit_scope(  # pyright: ignore[reportUnusedFunction]  # FastAPI registers by decorator.
        request: CockpitScopeActivationRequest,
        response: Response,
    ) -> CockpitSubjectScopeView:
        response.headers["Cache-Control"] = "no-store"
        view: Final = await cockpit_scope_call(
            "POST", "/api/cockpit/scope/activate", request.model_dump_json().encode()
        )
        scope_mismatch(request.subject_hash, view)
        return view

    @router.post("/cockpit/scope/deactivate")
    async def deactivate_cockpit_scope(  # pyright: ignore[reportUnusedFunction]  # FastAPI registers by decorator.
        request: CockpitScopeActivationRequest,
        response: Response,
    ) -> CockpitSubjectScopeView:
        response.headers["Cache-Control"] = "no-store"
        view: Final = await cockpit_scope_call(
            "POST", "/api/cockpit/scope/deactivate", request.model_dump_json().encode()
        )
        scope_mismatch(request.subject_hash, view)
        return view

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

    @router.delete("/environments/{card_id}/policy")
    async def reset_policy(card_id: UUID) -> PolicyView:
        return parse_response(
            await call("DELETE", f"/api/environments/{card_id}/policy"),
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
        request_id: UUID | None = None,
        refresh: bool = False,
    ) -> UpstreamSyncView:
        response.headers["Cache-Control"] = "no-store"
        query: Final = _upstream_sync_query(request_id, refresh)
        return parse_response(await call("GET", "/api/upstream-sync" + query), TypeAdapter(UpstreamSyncView))

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

    @router.get("/upstream-sync/litellm", response_model=UpstreamSyncView)
    async def litellm_upstream_sync_status(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        response: Response,
        request_id: UUID | None = None,
        refresh: bool = False,
    ) -> UpstreamSyncView:
        response.headers["Cache-Control"] = "no-store"
        query: Final = _upstream_sync_query(request_id, refresh)
        return parse_response(await call("GET", "/api/upstream-sync/litellm" + query), TypeAdapter(UpstreamSyncView))

    @router.post("/upstream-sync/litellm/analyze", response_model=UpstreamSyncDispatch, status_code=202)
    async def analyze_litellm_upstream(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        response: Response,
    ) -> UpstreamSyncDispatch:
        response.headers["Cache-Control"] = "no-store"
        return parse_response(
            await call("POST", "/api/upstream-sync/litellm/analyze"), TypeAdapter(UpstreamSyncDispatch)
        )

    @router.post("/upstream-sync/litellm/promote", response_model=UpstreamSyncDispatch, status_code=202)
    async def promote_litellm_upstream(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        response: Response,
    ) -> UpstreamSyncDispatch:
        response.headers["Cache-Control"] = "no-store"
        return parse_response(
            await call("POST", "/api/upstream-sync/litellm/promote"), TypeAdapter(UpstreamSyncDispatch)
        )

    @router.get("/upstream-sync/litellm/codex-review", response_model=CodexReviewPackage)
    async def litellm_codex_review_package(  # pyright: ignore[reportUnusedFunction]  # registered by FastAPI
        response: Response,
    ) -> CodexReviewPackage:
        response.headers["Cache-Control"] = "no-store"
        return parse_response(
            await call("GET", "/api/upstream-sync/litellm/codex-review"), TypeAdapter(CodexReviewPackage)
        )

    return router


T = TypeVar("T")


def parse_response(payload: bytes, adapter: TypeAdapter[T]) -> T:
    try:
        return adapter.validate_json(payload)
    except ValueError as error:
        raise HTTPException(502, "Account Pool Manager returned an invalid response") from error
