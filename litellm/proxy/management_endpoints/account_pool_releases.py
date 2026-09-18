"""本模块为管理员代理固定版本管理接口，部署令牌和 Docker 权限不交给浏览器。"""

import hashlib
import os
from collections.abc import Callable
from types import MappingProxyType
from typing import Annotated, Final, Literal, TypeVar

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ValidationError

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_release_models import (
    ReleaseAction,
    ReleaseCommands,
    ReleaseConfirmation,
    ReleaseExecute,
    ReleaseId,
    ReleaseJob,
    ReleaseView,
)

Result: Final = TypeVar("Result", bound=BaseModel)


class ReleaseFailure(BaseModel):
    detail: str


def create_release_router(
    require_admin: Callable[[UserAPIKeyAuth], None],
    client_factory: Callable[[], httpx.AsyncClient] = lambda: httpx.AsyncClient(timeout=30, trust_env=False),
) -> APIRouter:
    def authorize(user: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)], response: Response) -> str:
        require_admin(user)
        response.headers["Cache-Control"] = "no-store"  # rebind-ok: FastAPI 注入此响应对象用于设置返回头。
        return hashlib.sha256((user.user_id or user.api_key or "proxy-admin").encode()).hexdigest()

    router: Final = APIRouter(prefix="/releases")

    async def call(
        method: Literal["GET", "POST"],
        path: str,
        actor: str,
        model: type[Result],
        body: BaseModel | None = None,
    ) -> Result:
        token: Final = os.getenv("ACCOUNT_POOL_RELEASE_TOKEN", "")
        if len(token) < 32:
            raise HTTPException(503, "版本管理尚未启用，请按部署说明配置独立部署服务")
        try:
            async with client_factory() as client:
                result: Final = await client.request(
                    method,
                    "http://release-worker:8092/api/releases" + path,
                    headers=MappingProxyType(
                        {
                            "Authorization": "Bearer " + token,
                            "X-Release-Actor": actor,
                            "Content-Type": "application/json",
                        }
                    ),
                    content=body.model_dump_json().encode() if body else None,
                    timeout=300 if isinstance(body, ReleaseAction) and body.action == "apply" else 30,
                )
        except httpx.HTTPError as error:
            raise HTTPException(503, "部署管理服务暂时不可用；服务切换期间请稍后刷新") from error
        if result.status_code in (400, 404, 409, 422):
            try:
                failure: Final = ReleaseFailure.model_validate_json(result.content)
            except ValidationError as error:
                raise HTTPException(502, "部署管理响应无效") from error
            raise HTTPException(result.status_code, failure.detail)
        if result.is_error:
            raise HTTPException(502, "部署管理服务请求失败，请检查配置和运行状态")
        try:
            return model.model_validate_json(result.content)
        except ValidationError as error:
            raise HTTPException(502, "部署管理响应无效，请检查版本兼容性") from error

    async def view(actor: Annotated[str, Depends(authorize)]) -> ReleaseView:
        return await call("GET", "", actor, ReleaseView)

    async def prepare(action: ReleaseAction, actor: Annotated[str, Depends(authorize)]) -> ReleaseConfirmation:
        if action.action in ("deploy", "recover"):
            raise HTTPException(400, "新版本部署和故障恢复请使用服务器命令入口")
        return await call("POST", "/prepare", actor, ReleaseConfirmation, action)

    async def execute(body: ReleaseExecute, actor: Annotated[str, Depends(authorize)]) -> ReleaseJob:
        return await call("POST", "/execute", actor, ReleaseJob, body)

    async def commands(version_id: ReleaseId, actor: Annotated[str, Depends(authorize)]) -> ReleaseCommands:
        return await call("GET", f"/{version_id}/commands", actor, ReleaseCommands)

    router.get("")(view)
    router.post("/prepare")(prepare)
    router.post("/execute")(execute)
    router.get("/{version_id}/commands")(commands)
    return router
