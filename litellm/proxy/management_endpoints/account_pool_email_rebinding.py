"""转发管理员确认的邮箱换绑操作，复用统一 Manager 传输与权限，不进入推理链路。"""

from collections.abc import Callable
from types import MappingProxyType
from typing import Annotated, Final, Literal, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import TypeAdapter

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_automation import AutomationRoute
from litellm.proxy.management_endpoints.account_pool_automation_models import AutomationScreen, LoginScriptStatus
from litellm.proxy.management_endpoints.account_pool_email_rebinding_models import (
    BindingAction,
    BindingPoolView,
    BindingView,
    ConfirmBinding,
    MailboxCode,
    PoolImportResult,
    PoolUpload,
    ProposeBinding,
    RebindingScriptUpdate,
)
from litellm.proxy.management_endpoints.account_pool_management import ManagementRequest, parse_response

T = TypeVar("T")  # rebind-ok: TypeVar must remain unannotated for generic type recognition


def create_rebinding_router(
    request_manager: ManagementRequest, require_admin: Callable[[UserAPIKeyAuth], None]
) -> APIRouter:
    def authorize(user: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)]) -> None:
        require_admin(user)

    router: Final = APIRouter(
        prefix="/email-rebinding", dependencies=(Depends(authorize),), route_class=AutomationRoute
    )

    async def call(
        method: Literal["GET", "POST", "PUT"], path: str, model: TypeAdapter[T], body: bytes | None = None
    ) -> T:
        response: Final = await request_manager(method, "/api/email-rebinding" + path, body)
        if response.is_error:
            raise HTTPException(
                response.status_code,
                "换绑操作未完成，请检查原卡和任务进度；不会自动重复换绑",
                headers=MappingProxyType({"Cache-Control": "no-store"}),
            )
        return parse_response(response.content, model)

    async def view() -> BindingPoolView:
        return await call("GET", "", TypeAdapter(BindingPoolView))

    async def upload(request: PoolUpload) -> PoolImportResult:
        return await call("POST", "/mailboxes", TypeAdapter(PoolImportResult), request.model_dump_json().encode())

    async def export() -> Response:
        response: Final = await request_manager("GET", "/api/email-rebinding/export", None)
        if response.is_error:
            raise HTTPException(response.status_code, "邮箱状态导出未完成")
        return Response(
            response.content,
            media_type="text/csv; charset=utf-8",
            headers=MappingProxyType(
                {
                    "Content-Disposition": 'attachment; filename="mailbox-binding-status.csv"',
                    "Cache-Control": "no-store",
                }
            ),
        )

    async def propose(request: ProposeBinding) -> BindingView:
        return await call("POST", "/jobs", TypeAdapter(BindingView), request.model_dump_json().encode())

    async def confirm(job_id: UUID, request: ConfirmBinding) -> BindingView:
        return await call(
            "POST", f"/jobs/{job_id}/confirm", TypeAdapter(BindingView), request.model_dump_json().encode()
        )

    async def action(job_id: UUID, request: BindingAction) -> BindingView:
        return await call(
            "POST", f"/jobs/{job_id}/action", TypeAdapter(BindingView), request.model_dump_json().encode()
        )

    async def code(job_id: UUID, request: MailboxCode) -> BindingView:
        return await call("POST", f"/jobs/{job_id}/code", TypeAdapter(BindingView), request.model_dump_json().encode())

    async def screen(job_id: UUID) -> AutomationScreen:
        return await call("POST", f"/jobs/{job_id}/screen", TypeAdapter(AutomationScreen))

    async def script() -> LoginScriptStatus:
        return await call("GET", "/script", TypeAdapter(LoginScriptStatus))

    async def update_script(request: RebindingScriptUpdate) -> LoginScriptStatus:
        return await call("PUT", "/script", TypeAdapter(LoginScriptStatus), request.model_dump_json().encode())

    router.get("")(view)
    router.post("/mailboxes")(upload)
    router.get("/export")(export)
    router.post("/jobs", status_code=201)(propose)
    router.post("/jobs/{job_id}/confirm", status_code=202)(confirm)
    router.post("/jobs/{job_id}/action")(action)
    router.post("/jobs/{job_id}/code")(code)
    router.post("/jobs/{job_id}/screen")(screen)
    router.get("/script")(script)
    router.put("/script")(update_script)
    return router
