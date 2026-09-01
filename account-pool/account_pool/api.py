"""本模块暴露仅供 LiteLLM 调用的管理 API，以及经 SSH 隧道访问的 OAuth 回调。"""

from __future__ import annotations

import hmac
import html
from typing import Annotated, Final, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from account_pool.domain import (
    AuthorizationView,
    CreateEnvironmentRequest,
    EnvironmentView,
    GatewayEnvironment,
    OAuthCallback,
    ProxyProfile,
    UpdateEnvironmentRequest,
)
from account_pool.service import EnvironmentService, Failure, FailureCode, Result

_BEARER: Final = HTTPBearer(auto_error=False)
T = TypeVar("T")


def create_router(service: EnvironmentService, manager_token: str) -> APIRouter:
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

    @router.get("/api/environments", dependencies=[Depends(require_manager)])
    async def list_environments() -> tuple[EnvironmentView, ...]:
        return await service.list_environments()

    @router.post("/api/environments", dependencies=[Depends(require_manager)], response_model=AuthorizationView)
    async def create_environment(request: CreateEnvironmentRequest) -> AuthorizationView:
        return _unwrap(await service.create_environment(request))

    @router.get("/api/environments/{environment_id}", dependencies=[Depends(require_manager)])
    async def get_environment(environment_id: UUID) -> EnvironmentView:
        return _unwrap(await service.get_environment(environment_id))

    @router.put("/api/environments/{environment_id}", dependencies=[Depends(require_manager)])
    async def update_environment(environment_id: UUID, request: UpdateEnvironmentRequest) -> EnvironmentView:
        return _unwrap(await service.update_environment(environment_id, request))

    @router.delete("/api/environments/{environment_id}", dependencies=[Depends(require_manager)])
    async def delete_environment(environment_id: UUID) -> None:
        _unwrap(await service.delete_environment(environment_id))


        return await service.list_proxy_profiles()

    @router.get("/internal/gateway/environments", dependencies=[Depends(require_manager)], include_in_schema=False)
    async def list_gateway_environments() -> tuple[GatewayEnvironment, ...]:
        return await service.list_gateway_environments()

    @router.get("/auth/callback", response_class=HTMLResponse, include_in_schema=False)
    async def oauth_callback(
        state_value: Annotated[str, Query(alias="state", min_length=16, max_length=512)],
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
        result: Final = await service.submit_oauth_callback(callback)
        if isinstance(result, Failure):
            return HTMLResponse(_callback_page("授权未完成", result.message), status_code=_status_for(result.code))
        return HTMLResponse(_callback_page("授权已接收", "可以关闭此页面并返回 LiteLLM 号池"))

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
