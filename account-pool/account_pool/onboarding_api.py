"""提供管理员上号接口，密码仅通过显式读取返回并禁止缓存。"""

from collections.abc import Awaitable, Callable
from typing import Final, get_args
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from account_pool.channels.cliproxyapi.suppliers.registry import SupplierRegistry
from account_pool.domain import AuthorizationFlow, AuthorizationView
from account_pool.onboarding_models import (
    OnboardingAction,
    OnboardingImport,
    OnboardingImportResult,
    OnboardingItem,
    OnboardingPreview,
    OnboardingSecrets,
    OnboardingSupplier,
    OnboardingSupplierOption,
    OnboardingTarget,
    OnboardingTargetView,
)
from account_pool.onboarding_service import OnboardingService
from account_pool.provider_families import PROVIDER_FAMILIES
from account_pool.shared.result import Failure


class OnboardingRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler: Final = super().get_route_handler()

        async def validated(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                # FastAPI 默认错误会回显原始 input，批量密码和认证文件不能进入响应或追踪。
                raise HTTPException(422, "上号请求格式无效，请检查字段和文件大小") from None

        return validated


def create_onboarding_router(service: OnboardingService, authorize: Callable[..., None]) -> APIRouter:
    router: Final = APIRouter(prefix="/api/onboarding", dependencies=[Depends(authorize)], route_class=OnboardingRoute)

    @router.get("/suppliers")
    async def suppliers() -> tuple[OnboardingSupplierOption, ...]:
        registry: Final = SupplierRegistry.default()
        return tuple(
            OnboardingSupplierOption(
                supplier=family.supplier.value,
                display_name=family.display_name,
                authentication=family.authentication,
                oauth=family.available
                and family.supplier.value in get_args(OnboardingSupplier)
                and definition is not None
                and definition.authorization_flow is not AuthorizationFlow.DIRECT_CREDENTIAL,
                auth_file=family.available and family.supplier.value in get_args(OnboardingSupplier),
                description=family.description,
            )
            for family in PROVIDER_FAMILIES
            if family.supplier is not None
            for definition in (registry.definitions.get(family.supplier),)
        )

    @router.post("/preview")
    async def preview(request: OnboardingImport) -> tuple[OnboardingPreview, ...]:
        return await service.preview(request)

    @router.post("/imports", status_code=202)
    async def submit(request: OnboardingImport) -> OnboardingImportResult:
        return await service.submit(request)

    @router.get("/items")
    async def items() -> tuple[OnboardingItem, ...]:
        return await service.list()

    @router.post("/items/{item_id}/action")
    async def action(item_id: UUID, request: OnboardingAction) -> OnboardingItem:
        result: Final = await service.action(item_id, request)
        if isinstance(result, Failure):
            raise HTTPException(409, result.message)
        return result.value

    @router.post("/items/{item_id}/secrets")
    async def secrets(item_id: UUID, response: Response) -> OnboardingSecrets:
        response.headers["Cache-Control"] = "no-store"
        result: Final = await service.reveal(item_id)
        if isinstance(result, Failure):
            raise HTTPException(404, result.message)
        return result.value

    @router.get("/items/{item_id}/authorization")
    async def authorization(item_id: UUID, response: Response) -> AuthorizationView:
        response.headers["Cache-Control"] = "no-store"
        result: Final = await service.authorization(item_id)
        if isinstance(result, Failure):
            raise HTTPException(409, result.message)
        return result.value

    @router.get("/targets")
    async def targets() -> tuple[OnboardingTargetView, ...]:
        return await service.targets()

    @router.put("/targets")
    async def save_target(request: OnboardingTarget) -> OnboardingTarget:
        await service.repository.save_target(request)
        return request

    return router
