"""提供管理员邮箱池、显式确认和可恢复任务接口，不返回收信或浏览器控制秘密。"""

from collections.abc import Callable
from typing import Final, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from account_pool.email_rebinding.models import (
    BindingAction,
    BindingPoolView,
    BindingView,
    ConfirmBinding,
    MailboxCode,
    PoolImportResult,
    PoolUpload,
    ProposeBinding,
)
from account_pool.email_rebinding.runner import EmailBindingRunner
from account_pool.oauth_automation.api import AutomationRoute
from account_pool.oauth_automation.login_scripts import LoginScriptPackage, LoginScriptStatus, LoginScriptUpdate
from account_pool.oauth_automation.models import AutomationScreen
from account_pool.shared.result import Failure, FailureCode, Result

T = TypeVar("T")


def _unwrap(result: Result[T]) -> T:
    if isinstance(result, Failure):
        status: Final = {
            FailureCode.NOT_FOUND: 404,
            FailureCode.CONFLICT: 409,
            FailureCode.INVALID: 422,
            FailureCode.UPSTREAM: 502,
        }[result.code]
        raise HTTPException(status, result.message)
    return result.value


class RebindingScriptUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    publisher_key: SecretStr = Field(min_length=32, max_length=128, repr=False)
    package: LoginScriptPackage | None = Field(repr=False)


def create_rebinding_router(runner: EmailBindingRunner, authorize: Callable[..., None]) -> APIRouter:
    router: Final = APIRouter(
        prefix="/api/email-rebinding", dependencies=[Depends(authorize)], route_class=AutomationRoute
    )

    async def view() -> BindingPoolView:
        return await runner.service.view()

    async def upload(request: PoolUpload) -> PoolImportResult:
        return _unwrap(await runner.service.upload(request))

    async def export() -> Response:
        return Response(
            await runner.service.export(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="mailbox-binding-status.csv"',
                "Cache-Control": "no-store",
            },
        )

    async def propose(request: ProposeBinding) -> BindingView:
        return _unwrap(await runner.service.propose(request))

    async def confirm(job_id: UUID, request: ConfirmBinding) -> BindingView:
        return _unwrap(await runner.service.confirm(job_id, request))

    async def action(job_id: UUID, request: BindingAction) -> BindingView:
        return _unwrap(await runner.action(job_id, request.action))

    async def code(job_id: UUID, request: MailboxCode) -> BindingView:
        return _unwrap(await runner.code(job_id, request.code))

    async def screen(job_id: UUID) -> AutomationScreen:
        return _unwrap(await runner.screen(job_id))

    async def script() -> LoginScriptStatus:
        return runner.scripts.status()

    async def update_script(request: RebindingScriptUpdate) -> LoginScriptStatus:
        return _unwrap(
            runner.scripts.update(
                LoginScriptUpdate(
                    publisher_key=request.publisher_key,
                    package=request.package,
                )
            )
        )

    router.add_api_route("", view, methods=["GET"])
    router.add_api_route("/mailboxes", upload, methods=["POST"])
    router.add_api_route("/export", export, methods=["GET"])
    router.add_api_route("/jobs", propose, methods=["POST"], status_code=201)
    router.add_api_route("/jobs/{job_id}/confirm", confirm, methods=["POST"], status_code=202)
    router.add_api_route("/jobs/{job_id}/action", action, methods=["POST"])
    router.add_api_route("/jobs/{job_id}/code", code, methods=["POST"])
    router.add_api_route("/jobs/{job_id}/screen", screen, methods=["POST"])
    router.add_api_route("/script", script, methods=["GET"])
    router.add_api_route("/script", update_script, methods=["PUT"])
    return router
