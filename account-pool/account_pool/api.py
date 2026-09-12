"""本模块暴露仅供 LiteLLM 调用的管理 API，以及经 SSH 隧道访问的 OAuth 回调。"""

from __future__ import annotations

import asyncio
import hmac
import html
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated, Final, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from account_pool.batch_models import BatchJob, BatchRequest
from account_pool.batch_service import BatchService
from account_pool.card_keys import CardKeyService
from account_pool.clash import ClashError
from account_pool.contracts import AuthorizationView, EnvironmentView, GatewayEnvironment, ProxyProfile
from account_pool.domain import (
    ChannelKind,
    CreateDirectCredentialEnvironmentRequest,
    CreateEnvironmentRequest,
    CreateVertexEnvironmentRequest,
    EnvironmentRecord,
    OAuthCallback,
    OpenAICompatibleCredentialDeleteRequest,
    OpenAICompatibleCredentialRequest,
    UpdateEnvironmentRequest,
)
from account_pool.error_logs import ErrorLogService, ErrorStats
from account_pool.gateway_service import GatewayService, create_gateway_router
from account_pool.management_api import create_management_router
from account_pool.plugins import PluginManifest, PluginRecord, PluginService
from account_pool.policies import AccountPolicy, PolicyRepository
from account_pool.ports import EnvironmentRepository
from account_pool.provider_families import PROVIDER_FAMILIES
from account_pool.proxy_gateways import GatewayConfigurationView, GatewayDelayView, GatewayView
from account_pool.service import EnvironmentService, Failure, FailureCode, Result
from account_pool.settings import AccountPoolSettings, AccountPoolSettingsRepository

_BEARER: Final = HTTPBearer(auto_error=False)
T = TypeVar("T")


class GatewaySwitchRequest(BaseModel):
    node_name: str = Field(min_length=1, max_length=256)


class ClashNodeView(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    proxy_type: str


class ProviderFamilyView(BaseModel):
    """供应商家族的公开目录项，不包含密钥、代理地址或账号状态。"""

    model_config = ConfigDict(frozen=True)

    kind: str
    display_name: str
    supplier: str | None
    authentication: str
    available: bool
    description: str
    card_count: int = 0


class DashboardStatsView(BaseModel):
    """仪表盘聚合统计，只返回脱敏请求统计和按卡片拆分结果。"""

    model_config = ConfigDict(frozen=True)

    summary: ErrorStats
    cards: tuple[ErrorStats, ...]


class CredentialView(BaseModel):
    """凭据投影视图，只返回状态和统计，不暴露密钥或认证文件内容。"""

    model_config = ConfigDict(frozen=True)

    id: str
    card_id: UUID
    card_name: str
    supplier: str
    kind: str
    status: str
    enabled: bool
    model_count: int = 0
    auth_index: str | None = None


class QuotaRefreshResult(BaseModel):
    """额度刷新结果，只返回已更新的卡片投影。"""

    model_config = ConfigDict(frozen=True)

    refreshed: tuple[EnvironmentView, ...]
    failed_card_ids: tuple[UUID, ...] = ()


class AuthFileStatusRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    disabled: bool


class AuthFileFieldsRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fields: dict[str, object]


class PluginConfigRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")


class PluginInstallRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1, max_length=64)
    source: str | None = Field(default=None, min_length=1, max_length=120)


class PluginEnabledRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool


_MAX_VERTEX_CREDENTIAL_BYTES: Final = 1024 * 1024


def _validate_vertex_credential(content: bytes) -> None:
    if not content or len(content) > _MAX_VERTEX_CREDENTIAL_BYTES:
        raise HTTPException(status_code=422, detail="Vertex credential must be a JSON file no larger than 1 MiB")
    try:
        payload: Final = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=422, detail="Vertex credential must contain valid JSON") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Vertex credential must be a JSON object")
    required: Final = ("project_id", "client_email", "private_key")
    if any(not isinstance(payload.get(key), str) or not payload[key].strip() for key in required):
        raise HTTPException(
            status_code=422, detail="Vertex credential is missing project_id, client_email, or private_key"
        )


def create_router(
    service: EnvironmentService,
    manager_token: str,
    *,
    keys: CardKeyService | None = None,
    logs: ErrorLogService | None = None,
    environments: EnvironmentRepository | None = None,
    policies: PolicyRepository | None = None,
    gateway_service: GatewayService | None = None,
    batch_service: BatchService | None = None,
    settings: AccountPoolSettingsRepository | None = None,
    plugins: PluginService | None = None,
    sync_settings: Callable[[AccountPoolSettings], Awaitable[tuple[UUID, ...]]] | None = None,
    sync_policy: Callable[[EnvironmentRecord, AccountPolicy], Awaitable[None]] | None = None,
) -> APIRouter:
    router: Final = APIRouter()

    def require_manager(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_BEARER)],
    ) -> None:
        supplied: Final = "" if credentials is None else credentials.credentials
        if not hmac.compare_digest(supplied, manager_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid manager token")

    @router.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/api/provider-families", dependencies=[Depends(require_manager)])
    async def list_provider_families() -> tuple[ProviderFamilyView, ...]:
        records: Final = await service.list_environments()
        counts: Final = {
            family.kind: sum(1 for record in records if record.supplier.value == family.kind)
            for family in PROVIDER_FAMILIES
        }
        return tuple(
            ProviderFamilyView(
                kind=family.kind,
                display_name=family.display_name,
                supplier=family.supplier.value if family.supplier is not None else None,
                authentication=family.authentication,
                available=family.available,
                description=family.description,
                card_count=counts.get(family.kind, 0),
            )
            for family in PROVIDER_FAMILIES
        )

    @router.get("/api/environments", dependencies=[Depends(require_manager)])
    async def list_environments() -> tuple[EnvironmentView, ...]:
        return await service.list_environments()

    @router.post("/api/environments/{environment_id}/refresh", dependencies=[Depends(require_manager)])
    async def refresh_environment(environment_id: UUID) -> EnvironmentView:
        return _unwrap(await service.refresh_environment(environment_id))

    @router.post("/api/quotas/refresh", dependencies=[Depends(require_manager)])
    async def refresh_quotas() -> QuotaRefreshResult:
        if environments is None:
            return QuotaRefreshResult(refreshed=())
        records: Final = await environments.list()
        results: Final = await asyncio.gather(*(service.refresh_environment(record.id) for record in records))
        refreshed: Final = tuple(result.value for result in results if not isinstance(result, Failure))
        failed: Final = tuple(record.id for record, result in zip(records, results) if isinstance(result, Failure))
        return QuotaRefreshResult(refreshed=refreshed, failed_card_ids=failed)

    @router.get("/api/credentials", dependencies=[Depends(require_manager)])
    @router.get("/api/auth-files", dependencies=[Depends(require_manager)])
    async def list_credentials() -> tuple[CredentialView, ...]:
        if environments is None:
            return ()
        records: Final = await environments.list()
        return tuple(credential for record in records for credential in _credential_views(record))

    @router.post("/api/auth-files", dependencies=[Depends(require_manager)])
    async def upload_auth_file(
        card_id: Annotated[UUID, Form()],
        file: Annotated[UploadFile, File()],
    ) -> EnvironmentView:
        filename: Final = file.filename or "auth.json"
        if len(filename) > 256 or "\\" in filename or "/" in filename:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid auth file name")
        content: Final = await file.read(16 * 1024 * 1024 + 1)
        if len(content) > 16 * 1024 * 1024:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "auth file exceeds 16 MiB")
        return _unwrap(await service.upload_auth_file(card_id, filename, content, file.content_type))

    @router.get("/api/environments/{environment_id}/auth-file/download", dependencies=[Depends(require_manager)])
    async def download_auth_file(environment_id: UUID) -> StreamingResponse:
        content, content_type, filename = _unwrap(await service.download_auth_file(environment_id))
        return StreamingResponse(
            iter((content,)),
            media_type=content_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.delete("/api/environments/{environment_id}/auth-file", dependencies=[Depends(require_manager)])
    async def delete_auth_file(environment_id: UUID) -> EnvironmentView:
        return _unwrap(await service.delete_auth_file(environment_id))

    @router.patch("/api/environments/{environment_id}/auth-file/status", dependencies=[Depends(require_manager)])
    async def patch_auth_file_status(environment_id: UUID, request: AuthFileStatusRequest) -> EnvironmentView:
        return _unwrap(await service.patch_auth_file_status(environment_id, request.disabled))

    @router.patch("/api/environments/{environment_id}/auth-file/fields", dependencies=[Depends(require_manager)])
    async def patch_auth_file_fields(environment_id: UUID, request: AuthFileFieldsRequest) -> EnvironmentView:
        return _unwrap(await service.patch_auth_file_fields(environment_id, request.fields))

    @router.get("/api/environments/{environment_id}/auth-file/models", dependencies=[Depends(require_manager)])
    async def get_auth_file_models(environment_id: UUID) -> tuple[str, ...]:
        return _unwrap(await service.get_auth_file_models(environment_id))

    @router.post("/api/environments/{environment_id}/credentials", dependencies=[Depends(require_manager)])
    async def add_credential(
        environment_id: UUID,
        request: OpenAICompatibleCredentialRequest,
    ) -> EnvironmentView:
        return _unwrap(await service.add_openai_compatible_credential(environment_id, request))

    @router.delete("/api/environments/{environment_id}/credentials", dependencies=[Depends(require_manager)])
    async def delete_credential(
        environment_id: UUID,
        request: OpenAICompatibleCredentialDeleteRequest,
    ) -> EnvironmentView:
        return _unwrap(await service.delete_openai_compatible_credential(environment_id, request))

    @router.get("/api/dashboard", dependencies=[Depends(require_manager)])
    async def dashboard_stats() -> DashboardStatsView:
        if logs is None or environments is None:
            return DashboardStatsView(summary=ErrorStats(), cards=())
        records: Final = await environments.list()
        cards: Final = tuple(
            await asyncio.gather(*(logs.repository.stats(record.id, None, None) for record in records))
        )
        summary: Final = await logs.repository.stats(None, None, None)
        return DashboardStatsView(summary=summary, cards=cards)

    if plugins is not None:

        @router.get("/api/plugins", dependencies=[Depends(require_manager)])
        async def list_plugins() -> tuple[PluginRecord, ...]:
            return await plugins.installed()

        @router.get("/api/plugin-store", dependencies=[Depends(require_manager)])
        async def plugin_store() -> tuple[PluginManifest, ...]:
            return plugins.store()

        @router.post("/api/plugins", dependencies=[Depends(require_manager)])
        async def install_plugin(manifest: PluginManifest) -> PluginRecord:
            return await plugins.install(manifest)

        @router.post("/api/plugins/{plugin_id}/enable", dependencies=[Depends(require_manager)])
        async def enable_plugin(plugin_id: str) -> PluginRecord:
            record: Final = await plugins.set_enabled(plugin_id, True)
            if record is None:
                raise HTTPException(status_code=404, detail="plugin not found or incompatible")
            return record

        @router.post("/api/plugins/{plugin_id}/disable", dependencies=[Depends(require_manager)])
        async def disable_plugin(plugin_id: str) -> PluginRecord:
            record: Final = await plugins.set_enabled(plugin_id, False)
            if record is None:
                raise HTTPException(status_code=404, detail="plugin not found or incompatible")
            return record

        @router.delete("/api/plugins/{plugin_id}", dependencies=[Depends(require_manager)], status_code=204)
        async def uninstall_plugin(plugin_id: str) -> None:
            await plugins.uninstall(plugin_id)

    @router.get("/api/environments/{environment_id}/plugins", dependencies=[Depends(require_manager)])
    async def list_card_plugins(environment_id: UUID) -> Mapping[str, object]:
        return _unwrap(await service.list_card_plugins(environment_id))

    @router.get("/api/environments/{environment_id}/plugin-store", dependencies=[Depends(require_manager)])
    async def list_card_plugin_store(environment_id: UUID) -> Mapping[str, object]:
        return _unwrap(await service.list_card_plugin_store(environment_id))

    @router.post(
        "/api/environments/{environment_id}/plugins/{plugin_id}/install", dependencies=[Depends(require_manager)]
    )
    async def install_card_plugin(
        environment_id: UUID, plugin_id: str, request: PluginInstallRequest
    ) -> Mapping[str, object]:
        return _unwrap(await service.install_card_plugin(environment_id, plugin_id, request.version, request.source))

    @router.patch(
        "/api/environments/{environment_id}/plugins/{plugin_id}/enabled", dependencies=[Depends(require_manager)]
    )
    async def set_card_plugin_enabled(
        environment_id: UUID, plugin_id: str, request: PluginEnabledRequest
    ) -> Mapping[str, object]:
        return _unwrap(await service.set_card_plugin_enabled(environment_id, plugin_id, request.enabled))

    @router.delete("/api/environments/{environment_id}/plugins/{plugin_id}", dependencies=[Depends(require_manager)])
    async def uninstall_card_plugin(environment_id: UUID, plugin_id: str) -> Mapping[str, object]:
        return _unwrap(await service.uninstall_card_plugin(environment_id, plugin_id))

    @router.get(
        "/api/environments/{environment_id}/plugins/{plugin_id}/config", dependencies=[Depends(require_manager)]
    )
    async def get_card_plugin_config(environment_id: UUID, plugin_id: str) -> Mapping[str, object]:
        return _unwrap(await service.get_card_plugin_config(environment_id, plugin_id))

    @router.put(
        "/api/environments/{environment_id}/plugins/{plugin_id}/config", dependencies=[Depends(require_manager)]
    )
    async def put_card_plugin_config(
        environment_id: UUID, plugin_id: str, request: PluginConfigRequest
    ) -> Mapping[str, object]:
        return _unwrap(await service.put_card_plugin_config(environment_id, plugin_id, request.model_extra or {}))

    @router.post("/api/environments", dependencies=[Depends(require_manager)], response_model=AuthorizationView)
    async def create_environment(
        request: CreateEnvironmentRequest,
        operation_id: Annotated[str | None, Header(alias="Idempotency-Key", max_length=160)] = None,
    ) -> AuthorizationView:
        effective: Final = (
            request if operation_id is None else request.model_copy(update={"operation_id": operation_id})
        )
        return _unwrap(await service.create_environment(effective))

    @router.post("/api/openai-compatible", dependencies=[Depends(require_manager)])
    async def create_openai_compatible(
        request: CreateEnvironmentRequest,
        operation_id: Annotated[str | None, Header(alias="Idempotency-Key", max_length=160)] = None,
    ) -> EnvironmentView:
        effective: Final = (
            request if operation_id is None else request.model_copy(update={"operation_id": operation_id})
        )
        return _unwrap(await service.create_openai_compatible(effective))

    @router.post("/api/direct-credentials", dependencies=[Depends(require_manager)])
    async def create_direct_credential(
        request: CreateDirectCredentialEnvironmentRequest,
        operation_id: Annotated[str | None, Header(alias="Idempotency-Key", max_length=160)] = None,
    ) -> EnvironmentView:
        effective: Final = (
            request if operation_id is None else request.model_copy(update={"operation_id": operation_id})
        )
        return _unwrap(await service.create_direct_credential_environment(effective))

    @router.post("/api/vertex", dependencies=[Depends(require_manager)])
    async def create_vertex(
        name: Annotated[str, Form(min_length=1, max_length=80)],
        file: Annotated[UploadFile, File()],
        location: Annotated[str, Form(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")] = "us-central1",
        operation_id: Annotated[str | None, Header(alias="Idempotency-Key", max_length=160)] = None,
    ) -> EnvironmentView:
        content: Final = await file.read(_MAX_VERTEX_CREDENTIAL_BYTES + 1)
        _validate_vertex_credential(content)
        try:
            request: Final = CreateVertexEnvironmentRequest(
                name=name,
                location=location,
                operation_id=operation_id,
            )
        except ValidationError as error:
            raise HTTPException(status_code=422, detail="Vertex environment request is invalid") from error
        filename: Final = file.filename or "vertex-service-account.json"
        return _unwrap(await service.create_vertex_environment(request, filename, content))

    @router.get("/api/environments/{environment_id}", dependencies=[Depends(require_manager)])
    async def get_environment(environment_id: UUID) -> EnvironmentView:
        return _unwrap(await service.get_environment(environment_id))

    @router.put("/api/environments/{environment_id}", dependencies=[Depends(require_manager)])
    async def update_environment(
        environment_id: UUID,
        request: UpdateEnvironmentRequest,
        operation_id: Annotated[str | None, Header(alias="Idempotency-Key", max_length=160)] = None,
    ) -> EnvironmentView:
        effective: Final = (
            request if operation_id is None else request.model_copy(update={"operation_id": operation_id})
        )
        return _unwrap(await service.update_environment(environment_id, effective))

    @router.post("/api/environments/{environment_id}/authorize", dependencies=[Depends(require_manager)])
    async def authorize_environment(
        environment_id: UUID,
        operation_id: Annotated[str | None, Header(alias="Idempotency-Key", max_length=160)] = None,
    ) -> AuthorizationView:
        return _unwrap(await service.authorize_environment(environment_id, operation_id))

    @router.delete("/api/environments/{environment_id}/oauth-session", dependencies=[Depends(require_manager)])
    async def cancel_oauth_session(environment_id: UUID) -> EnvironmentView:
        return _unwrap(await service.cancel_oauth_session(environment_id))

    @router.delete("/api/environments/{environment_id}", dependencies=[Depends(require_manager)])
    async def delete_environment(
        environment_id: UUID,
        operation_id: Annotated[str | None, Header(alias="Idempotency-Key", max_length=160)] = None,
    ) -> None:
        _unwrap(await service.delete_environment(environment_id, operation_id))

    @router.get("/api/proxy-profiles", dependencies=[Depends(require_manager)])
    async def list_proxy_profiles() -> tuple[ProxyProfile, ...]:
        return await service.list_proxy_profiles()

    @router.get("/api/proxy-gateways", dependencies=[Depends(require_manager)])
    async def list_proxy_gateways() -> tuple[GatewayView, ...]:
        try:
            return await service.list_proxy_gateways()
        except ClashError as error:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error

    @router.post("/api/proxy-gateways/delay", dependencies=[Depends(require_manager)])
    async def measure_proxy_gateway_delays() -> tuple[GatewayDelayView, ...]:
        try:
            return await service.measure_proxy_gateway_delays()
        except ClashError as error:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error

    @router.get("/api/proxy-gateways/configuration", dependencies=[Depends(require_manager)])
    async def get_proxy_gateway_configuration() -> GatewayConfigurationView:
        return service.proxy_gateway_configuration()

    @router.get("/api/proxy-gateways/nodes", dependencies=[Depends(require_manager)])
    async def list_clash_nodes() -> tuple[ClashNodeView, ...]:
        try:
            nodes: Final = await service.list_clash_nodes()
        except ClashError as error:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error
        return tuple(ClashNodeView(name=node.name, proxy_type=node.proxy_type) for node in nodes)

    @router.put("/api/proxy-gateways/{port}", dependencies=[Depends(require_manager)])
    async def switch_proxy_gateway(
        port: int,
        request: GatewaySwitchRequest,
    ) -> GatewayView:
        try:
            return await service.switch_proxy_gateway(port, request.node_name)
        except ClashError as error:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error

    @router.get("/internal/gateway/environments", dependencies=[Depends(require_manager)], include_in_schema=False)
    async def list_gateway_environments() -> tuple[GatewayEnvironment, ...]:
        return await service.list_gateway_environments()

    @router.get("/auth/callback", response_class=HTMLResponse, include_in_schema=False)
    @router.get("/callback", response_class=HTMLResponse, include_in_schema=False)
    @router.get("/oauth-callback", response_class=HTMLResponse, include_in_schema=False)
    async def oauth_callback(
        state_value: Annotated[str, Query(alias="state", min_length=16, max_length=512)],
        environment_id: Annotated[UUID | None, Query()] = None,
        code: Annotated[str | None, Query(max_length=8192)] = None,
        error: Annotated[str | None, Query(max_length=512)] = None,
        error_description: Annotated[str | None, Query(max_length=2048)] = None,
    ) -> HTMLResponse:
        callback: Final = OAuthCallback(
            state=state_value,
            code=code,
            error=error,
            error_description=error_description,
        )
        result: Final = await service.submit_oauth_callback(callback, environment_id)
        if isinstance(result, Failure):
            return HTMLResponse(_callback_page("授权未完成", result.message), status_code=_status_for(result.code))
        return HTMLResponse(_callback_page("授权已接收", "可以关闭此页面并返回 LiteLLM 号池"))

    if keys is not None and logs is not None and environments is not None and policies is not None:
        router.include_router(
            create_management_router(
                keys,
                logs,
                environments,
                require_manager,
                policies,
                settings,
                sync_settings,
                sync_policy,
            )
        )
    if gateway_service is not None:
        router.include_router(create_gateway_router(gateway_service, require_manager))
    if batch_service is not None:
        router.include_router(create_batch_router(batch_service, require_manager))
    return router


def create_batch_router(service: BatchService, authorize: Callable[..., None]) -> APIRouter:
    router: Final = APIRouter(prefix="/api/batches", dependencies=[Depends(authorize)])

    async def submit_batch(request: BatchRequest, response: Response) -> BatchJob:
        response.headers["Cache-Control"] = "no-store"
        if not await service.submit(request):
            raise HTTPException(status_code=409, detail="Batch target is missing or job id is already used")
        job: Final = await service.get(request.job_id)
        if job is None:
            raise HTTPException(status_code=500, detail="Batch job was not persisted")
        return job

    async def list_batches(response: Response) -> tuple[BatchJob, ...]:
        response.headers["Cache-Control"] = "no-store"
        return await service.list()

    async def get_batch(job_id: UUID, response: Response) -> BatchJob:
        response.headers["Cache-Control"] = "no-store"
        job: Final = await service.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Batch job not found")
        return job

    router.add_api_route("", submit_batch, methods=["POST"], response_model=BatchJob, status_code=202)
    router.add_api_route("", list_batches, methods=["GET"], response_model=tuple[BatchJob, ...])
    router.add_api_route("/{job_id}", get_batch, methods=["GET"], response_model=BatchJob)
    return router


def _unwrap(result: Result[T]) -> T:
    if isinstance(result, Failure):
        raise HTTPException(status_code=_status_for(result.code), detail=result.message)
    return result.value


def _status_for(code: FailureCode) -> int:
    statuses: Final = {
        FailureCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
        FailureCode.CONFLICT: status.HTTP_409_CONFLICT,
        FailureCode.INVALID: status.HTTP_422_UNPROCESSABLE_CONTENT,
        FailureCode.UPSTREAM: status.HTTP_502_BAD_GATEWAY,
    }
    return statuses[code]


def _callback_page(title: str, message: str) -> str:
    safe_title: Final = html.escape(title)
    safe_message: Final = html.escape(message)
    return (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        f"<title>{safe_title}</title><body><main><h1>{safe_title}</h1><p>{safe_message}</p></main></body></html>"
    )


def _credential_views(record: EnvironmentRecord) -> tuple[CredentialView, ...]:
    if record.channel is ChannelKind.FREEBUFF2API:
        return ()
    if record.channel is ChannelKind.OPENAI_COMPATIBLE and record.openai_compatible is not None:
        return tuple(
            CredentialView(
                id=f"{record.id}:key-{index}",
                card_id=record.id,
                card_name=record.name,
                supplier=record.supplier.value,
                kind="api_key",
                status="enabled" if record.enabled else "disabled",
                enabled=record.enabled,
                model_count=len(record.available_models),
                auth_index=str(index),
            )
            for index, _ in enumerate(record.openai_compatible.credentials, start=1)
        )
    if record.auth_file_name is None:
        return ()
    return (
        CredentialView(
            id=f"{record.id}:{record.auth_index or 'default'}",
            card_id=record.id,
            card_name=record.name,
            supplier=record.supplier.value,
            kind="oauth_file",
            status=record.status.value,
            enabled=record.enabled,
            model_count=len(record.available_models),
            auth_index=record.auth_index,
        ),
    )
