"""本模块暴露仅供 LiteLLM 调用的管理 API，以及经 SSH 隧道访问的 OAuth 回调。"""

from __future__ import annotations

import asyncio
import hmac
import html
from collections.abc import Callable
from typing import Annotated, Final, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from account_pool.batch_models import BatchJob, BatchRequest
from account_pool.batch_service import BatchService
from account_pool.card_keys import CardKeyService
from account_pool.clash import ClashError
from account_pool.contracts import AuthorizationView, EnvironmentView, GatewayEnvironment, ProxyProfile
from account_pool.domain import CreateEnvironmentRequest, OAuthCallback, UpdateEnvironmentRequest
from account_pool.error_logs import ErrorLogService, ErrorStats
from account_pool.gateway_service import GatewayService, create_gateway_router
from account_pool.management_api import create_management_router
from account_pool.policies import PolicyRepository
from account_pool.ports import EnvironmentRepository
from account_pool.provider_families import PROVIDER_FAMILIES
from account_pool.proxy_gateways import GatewayConfigurationView, GatewayDelayView, GatewayView
from account_pool.service import EnvironmentService, Failure, FailureCode, Result

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
        counts: Final = {family.kind: sum(1 for record in records if record.supplier.value == family.kind) for family in PROVIDER_FAMILIES}
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
        effective: Final = request if operation_id is None else request.model_copy(update={"operation_id": operation_id})
        return _unwrap(await service.create_openai_compatible(effective))

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
