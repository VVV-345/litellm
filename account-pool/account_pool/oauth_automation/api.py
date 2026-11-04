"""暴露管理员授权自动化接口，严格投影公开字段，验证失败不回显登录资料。"""

from collections.abc import Callable, Coroutine
from typing import Final, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from account_pool.oauth_automation.models import (
    AutomationScreen,
    AutomationView,
    JobAction,
    JobInput,
    JobView,
    ProfileInput,
    ProfileUpdate,
    ProfileView,
    SettingsUpdate,
)
from account_pool.oauth_automation.runner import AutomationRunner
from account_pool.shared.result import Failure, FailureCode, Result

T = TypeVar("T")


class AutomationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler: Final = super().get_route_handler()

        async def safe(request: Request) -> Response:
            try:
                response: Final = await handler(request)
                response.headers["Cache-Control"] = "no-store"
                return response
            except RequestValidationError:
                raise HTTPException(
                    422, "自动化请求格式无效，请检查字段与资料大小", headers={"Cache-Control": "no-store"}
                ) from None
            except HTTPException as error:
                raise HTTPException(
                    error.status_code, error.detail, headers={**(error.headers or {}), "Cache-Control": "no-store"}
                ) from None

        return safe


def _unwrap(result: Result[T]) -> T:
    if isinstance(result, Failure):
        code: Final = {
            FailureCode.NOT_FOUND: 404,
            FailureCode.CONFLICT: 409,
            FailureCode.INVALID: 422,
            FailureCode.UPSTREAM: 502,
        }[result.code]
        raise HTTPException(code, result.message)
    return result.value


def create_automation_router(runner: AutomationRunner, authorize: Callable[..., None]) -> APIRouter:
    registry: Final = runner.registry
    router: Final = APIRouter(
        prefix="/api/oauth-automation", dependencies=[Depends(authorize)], route_class=AutomationRoute
    )

    async def view() -> AutomationView:
        return await registry.view()

    async def self_check() -> AutomationView:
        return _unwrap(await registry.self_check())

    async def settings(request: SettingsUpdate) -> AutomationView:
        return _unwrap(await registry.save_settings(request.settings, request.version))

    async def create_profile(request: ProfileInput) -> ProfileView:
        return _unwrap(await registry.create_profile(request))

    async def update_profile(profile_id: UUID, request: ProfileUpdate) -> ProfileView:
        return _unwrap(await registry.update_profile(profile_id, request))

    async def delete_profile(profile_id: UUID) -> AutomationView:
        return _unwrap(await registry.delete_profile(profile_id))

    async def submit(request: JobInput) -> JobView:
        return _unwrap(await registry.submit(request))

    async def cancel(job_id: UUID) -> JobView:
        return _unwrap(await registry.cancel(job_id))

    async def action(job_id: UUID, request: JobAction) -> JobView:
        return _unwrap(await runner.action(job_id, request.action, model=request.model))

    async def screen(job_id: UUID) -> AutomationScreen:
        return _unwrap(await runner.screen(job_id))

    router.add_api_route("", view, methods=["GET"])
    router.add_api_route("/self-check", self_check, methods=["POST"])
    router.add_api_route("/settings", settings, methods=["PUT"])
    router.add_api_route("/profiles", create_profile, methods=["POST"], status_code=201)
    router.add_api_route("/profiles/{profile_id}", update_profile, methods=["PUT"])
    router.add_api_route("/profiles/{profile_id}", delete_profile, methods=["DELETE"])
    router.add_api_route("/jobs", submit, methods=["POST"], status_code=202)
    router.add_api_route("/jobs/{job_id}/cancel", cancel, methods=["POST"])
    router.add_api_route("/jobs/{job_id}/action", action, methods=["POST"])
    router.add_api_route("/jobs/{job_id}/screen", screen, methods=["POST"])
    return router
