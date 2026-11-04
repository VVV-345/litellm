"""代理授权自动化管理，复用管理员鉴权和浏览器票据，不进入模型请求链路。"""

from collections.abc import Callable, Coroutine
from typing import Annotated, Final, Literal, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import TypeAdapter

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_automation_models import (
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
from litellm.proxy.management_endpoints.account_pool_management import ManagementRequest, parse_response

T = TypeVar("T")


class AutomationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler: Final = super().get_route_handler()

        async def validated(request: Request) -> Response:
            try:
                response: Final = await handler(request)
                response.headers["Cache-Control"] = "no-store"
                return response
            except RequestValidationError:
                raise HTTPException(
                    422, "自动授权请求格式无效，请检查字段和资料大小", headers={"Cache-Control": "no-store"}
                ) from None

        return validated


def create_automation_router(
    request_manager: ManagementRequest, require_admin: Callable[[UserAPIKeyAuth], None]
) -> APIRouter:
    def authorize(user: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)]) -> None:
        require_admin(user)

    router: Final = APIRouter(
        prefix="/oauth-automation", dependencies=[Depends(authorize)], route_class=AutomationRoute
    )

    async def call(
        method: Literal["GET", "POST", "PUT", "DELETE"], path: str, model: TypeAdapter[T], body: bytes | None = None
    ) -> T:
        response: Final = await request_manager(method, "/api/oauth-automation" + path, body)
        if response.is_error:
            raise HTTPException(
                response.status_code,
                "授权自动化操作未完成，请检查任务状态或 Manager 日志",
                headers={"Cache-Control": "no-store"},
            )
        return parse_response(response.content, model)

    async def view() -> AutomationView:
        return await call("GET", "", TypeAdapter(AutomationView))

    async def self_check() -> AutomationView:
        return await call("POST", "/self-check", TypeAdapter(AutomationView))

    async def settings(request: SettingsUpdate) -> AutomationView:
        return await call("PUT", "/settings", TypeAdapter(AutomationView), request.model_dump_json().encode())

    async def create_profile(request: ProfileInput) -> ProfileView:
        return await call("POST", "/profiles", TypeAdapter(ProfileView), request.model_dump_json().encode())

    async def update_profile(profile_id: UUID, request: ProfileUpdate) -> ProfileView:
        return await call(
            "PUT", f"/profiles/{profile_id}", TypeAdapter(ProfileView), request.model_dump_json().encode()
        )

    async def delete_profile(profile_id: UUID) -> AutomationView:
        return await call("DELETE", f"/profiles/{profile_id}", TypeAdapter(AutomationView))

    async def submit(request: JobInput) -> JobView:
        return await call("POST", "/jobs", TypeAdapter(JobView), request.model_dump_json().encode())

    async def action(job_id: UUID, request: JobAction) -> JobView:
        return await call("POST", f"/jobs/{job_id}/action", TypeAdapter(JobView), request.model_dump_json().encode())

    async def screen(job_id: UUID) -> AutomationScreen:
        return await call("POST", f"/jobs/{job_id}/screen", TypeAdapter(AutomationScreen))

    router.add_api_route("", view, methods=["GET"])
    router.add_api_route("/self-check", self_check, methods=["POST"])
    router.add_api_route("/settings", settings, methods=["PUT"])
    router.add_api_route("/profiles", create_profile, methods=["POST"], status_code=201)
    router.add_api_route("/profiles/{profile_id}", update_profile, methods=["PUT"])
    router.add_api_route("/profiles/{profile_id}", delete_profile, methods=["DELETE"])
    router.add_api_route("/jobs", submit, methods=["POST"], status_code=202)
    router.add_api_route("/jobs/{job_id}/action", action, methods=["POST"])
    router.add_api_route("/jobs/{job_id}/screen", screen, methods=["POST"])
    return router
