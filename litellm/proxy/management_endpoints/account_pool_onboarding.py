"""代理自动化上号管理接口，沿用 LiteLLM 管理员鉴权，不进入推理链路。"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Final, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import TypeAdapter

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_management import ManagementRequest, parse_response
from litellm.proxy.management_endpoints.account_pool_onboarding_models import (
    OnboardingAction,
    OnboardingAuthorization,
    OnboardingImport,
    OnboardingImportResult,
    OnboardingItem,
    OnboardingPreview,
    OnboardingSecrets,
    OnboardingTarget,
    OnboardingTargetView,
)


class OnboardingRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler: Final = super().get_route_handler()

        async def validated(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, "上号请求格式无效，请检查字段和文件大小") from None

        return validated


def create_onboarding_router(
    request_manager: ManagementRequest, require_admin: Callable[[UserAPIKeyAuth], None]
) -> APIRouter:
    def authorize(user: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)]) -> None:
        require_admin(user)

    router: Final = APIRouter(prefix="/onboarding", dependencies=[Depends(authorize)], route_class=OnboardingRoute)

    async def call(method: Literal["GET", "POST", "PUT"], path: str, body: bytes | None = None) -> bytes:
        response: Final = await request_manager(method, "/api/onboarding" + path, body)
        if response.is_error:
            raise HTTPException(response.status_code, "上号操作未完成，请刷新后重试或检查卡片状态")
        return response.content

    @router.post("/preview")
    async def preview(request: OnboardingImport) -> tuple[OnboardingPreview, ...]:
        return parse_response(
            await call("POST", "/preview", request.model_dump_json().encode()),
            TypeAdapter(tuple[OnboardingPreview, ...]),
        )

    @router.post("/imports", status_code=202)
    async def submit(request: OnboardingImport) -> OnboardingImportResult:
        return parse_response(
            await call("POST", "/imports", request.model_dump_json().encode()), TypeAdapter(OnboardingImportResult)
        )

    @router.get("/items")
    async def items() -> tuple[OnboardingItem, ...]:
        return parse_response(await call("GET", "/items"), TypeAdapter(tuple[OnboardingItem, ...]))

    @router.post("/items/{item_id}/action")
    async def action(item_id: UUID, request: OnboardingAction) -> OnboardingItem:
        return parse_response(
            await call("POST", f"/items/{item_id}/action", request.model_dump_json().encode()),
            TypeAdapter(OnboardingItem),
        )

    @router.post("/items/{item_id}/secrets")
    async def secrets(item_id: UUID, response: Response) -> OnboardingSecrets:
        response.headers["Cache-Control"] = "no-store"
        return parse_response(await call("POST", f"/items/{item_id}/secrets"), TypeAdapter(OnboardingSecrets))

    @router.get("/items/{item_id}/authorization")
    async def authorization(item_id: UUID, response: Response) -> OnboardingAuthorization:
        response.headers["Cache-Control"] = "no-store"
        return parse_response(
            await call("GET", f"/items/{item_id}/authorization"), TypeAdapter(OnboardingAuthorization)
        )

    @router.get("/targets")
    async def targets() -> tuple[OnboardingTargetView, ...]:
        return parse_response(await call("GET", "/targets"), TypeAdapter(tuple[OnboardingTargetView, ...]))

    @router.put("/targets")
    async def save_target(request: OnboardingTarget) -> OnboardingTarget:
        return parse_response(
            await call("PUT", "/targets", request.model_dump_json().encode()), TypeAdapter(OnboardingTarget)
        )

    return router
