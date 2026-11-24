"""代理授权自动化管理，复用管理员鉴权和浏览器票据，不进入模型请求链路。"""

import json
from collections.abc import Callable, Coroutine
from types import MappingProxyType
from typing import Annotated, Final, Literal, TypedDict, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import TypeAdapter
from typing_extensions import ReadOnly

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_automation_models import (
    AutomationScreen,
    AutomationView,
    JobAction,
    JobInput,
    JobView,
    LoginScriptStatus,
    LoginScriptUpdate,
    ProfileInput,
    ProfileUpdate,
    ProfileView,
    SettingsUpdate,
)
from litellm.proxy.management_endpoints.account_pool_management import ManagementRequest, parse_response

T = TypeVar("T")


# 只公开审核过的固定提示；不能将任意 Manager detail 当成已脱敏内容。
_OPERATOR_ERRORS: Final = MappingProxyType({
    "凭据正在验收，无需重新登录；可取消任务或结束后仅重试验收": "verification_in_progress",
    "请选择一条 email----password----TOTPBase32 登录资料，不接收多账号混合记录": "invalid_credentials",
    "浏览器镜像自检失败；检查镜像、显示服务和预装资源，不会启动账号登录": "image_check_failed",
    "请先配置按 digest 固定的服务端浏览器镜像": "image_not_configured",
    "新镜像必须先通过自检才能开启自动化": "image_not_checked",
    "请先通过当前浏览器镜像自检并开启自动化": "automation_not_ready",
    "配置已改变，请刷新后再保存": "settings_conflict",
    "没有可用的未绑定指纹，请添加或启用该账号原有指纹": "profile_unavailable",
    "原卡片仍存在，不允许把同一账号分给两张卡": "account_already_bound",
    "卡片已有凭据、停用或处于手动冷却，不覆盖现有认证": "card_not_empty",
    "请先保存并同步卡片的普通出口代理配置": "configuration_pending",
    "所选出口代理不可用，未创建卡片": "proxy_unavailable",
    "卡片或代理尚未就绪，或上次任务还在清理": "card_not_ready",
    "卡片仍有可用凭据或账号无法确认，未启动重新授权": "reauthorization_not_ready",
    "已保存的登录资料与当前指纹绑定不一致": "identity_mismatch",
    "这张卡片已有排队或运行中的任务": "job_exists",
    "卡片已有其他账号的指纹；若从未登录，先释放未登录预留": "identity_conflict",
    "原卡片已删除，请显式选择迁移原指纹或删除失效绑定": "profile_transfer_required",
    "任务正在推进，请稍后打开画面": "job_busy",
    "浏览器未就绪或已结束，不开放画面": "screen_not_available",
    "浏览器未就绪或已结束，不能接管或交回": "takeover_not_available",
    "浏览器会话已过期": "session_expired",
    "自动化请求格式无效，请检查字段与资料大小": "invalid_request",
    "自动授权请求格式无效，请检查字段和资料大小": "invalid_request",
    "指纹配置超过镜像支持的大小": "profile_size_exceeded",
    "新卡片和授权任务必须使用同一个操作标识": "operation_rejected",
    "指纹生成或验证失败，请先自检固定镜像": "profile_generation_failed",
    "指纹配置已满，请先整理未使用的配置": "operation_rejected",
    "指纹配置不存在": "operation_rejected",
    "指纹配置已改变，请刷新": "operation_rejected",
    "指纹分配发生并发冲突，请稍后重试": "operation_rejected",
    "运行中的指纹配置不能修改": "operation_rejected",
    "固定指纹的系统和语言不可变，请创建新的未绑定配置": "operation_rejected",
    "只能迁移与提交资料为同一账号的原指纹": "operation_rejected",
    "任务记录已达到 200 条上限，暂不能继续添加": "operation_rejected",
    "卡片不存在": "operation_rejected",
    "原卡或指纹已改变；邮箱已绑定，保留进度等待核对": "operation_rejected",
    "原卡已有其它授权任务": "operation_rejected",
    "保存的原账号资料与换绑任务不一致": "operation_rejected",
    "原卡已改变，未覆盖凭据": "operation_rejected",
    "任务正在推进，请稍后再忘记登录资料": "operation_rejected",
    "任务正在推进，请稍后再释放预留": "operation_rejected",
    "所选出口代理不可用，未绑定指纹或启动登录": "operation_rejected",
    "代理地区、语言或时区匹配失败；未绑定指纹或启动登录，请检查出口及固定镜像": "proxy_location_failed",
    "任务不存在": "operation_rejected",
    "任务正在推进，请稍后刷新再删除": "operation_rejected",
    "指纹仍有排队、运行或待清理任务，暂不能删除": "operation_rejected",
    "任务标识已被其他输入使用": "operation_rejected",
    "新卡片操作标识已占用": "operation_rejected",
    "请选择已有的空白 Codex 卡片": "operation_rejected",
    "仅支持 Codex 浏览器 OAuth": "operation_rejected",
    "原指纹还有未停止或未清理的任务": "operation_rejected",
    "卡片状态不允许重新授权": "operation_rejected",
    "换绑任务归属不一致": "operation_rejected",
    "先结束并清理授权任务，再忘记登录资料": "operation_rejected",
    "已确认的指纹不能作为未登录预留释放": "operation_rejected",
    "登录已开始或尚未清理，不能把指纹给其他账号": "operation_rejected",
    "只能删除已结束且清理完成的任务记录": "operation_rejected",
    "指纹仍绑定现有卡片，不能删除或更换登录身份": "operation_rejected",
    "指纹绑定状态不完整，请先核实原卡片和任务": "operation_rejected",
    "任务尚未结束或清理未完成，不能重试": "operation_rejected",
    "请先通过当前镜像自检并开启自动化": "operation_rejected",
    "已有运行任务或同卡排队任务，请等待后再重试": "operation_rejected",
    "任务正在推进，请稍后刷新再操作": "operation_rejected",
    "浏览器状态已改变，请刷新任务": "operation_rejected",
    "原卡片已删除，不会自动重建": "operation_rejected",
    "卡片、代理或运行状态不允许重试": "operation_rejected",
    "原指纹预留已释放，请选择原卡重新提交登录资料": "operation_rejected",
    "只能选择当前卡片已启用的模型重试验收，不重新登录": "operation_rejected",
    "已忘记登录资料，请在原卡提交新的登录资料": "operation_rejected",
    "回调已经消费或凭据状态不明，请先核实已有认证；不会再次登录覆盖凭据": "operation_rejected",
    "卡片已改变，请刷新": "operation_rejected",
    "没有等待清理的已结束任务": "operation_rejected",
    "浏览器已结束，请刷新任务": "operation_rejected",
    "接管通道不可用，任务已停止": "operation_rejected",
    "发布密钥未配置或不正确": "operation_rejected",
    "已发布脚本包无法读取或校验，请重新发布或切回内置脚本": "operation_rejected",
    "检查入口、相对路径和脚本包大小；未替换当前脚本": "operation_rejected",
    "脚本包写入失败，请检查 Manager 数据目录": "operation_rejected",
    "脚本发布未配置": "operation_rejected",
})


class OperatorErrorDetail(TypedDict):
    code: ReadOnly[str]
    message: ReadOnly[str]


def _automation_error_detail(content: bytes) -> OperatorErrorDetail:
    try:
        payload: Final = TypeAdapter(dict[str, object]).validate_json(content)
        message: Final = payload.get("detail")
        if isinstance(message, str) and message in _OPERATOR_ERRORS:
            detail: Final[OperatorErrorDetail] = {"code": _OPERATOR_ERRORS[message], "message": message}
            return detail
    except ValueError:
        pass
    fallback: Final[OperatorErrorDetail] = {
        "code": "operation_failed", "message": "授权自动化操作未完成，请检查任务状态或 Manager 日志",
    }
    return fallback


class AutomationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler: Final = super().get_route_handler()

        async def validated(request: Request) -> Response:
            try:
                response: Final = await handler(request)
                response.headers["Cache-Control"] = "no-store"
                return response
            except RequestValidationError:
                detail: Final[OperatorErrorDetail] = {
                    "code": "invalid_request", "message": "自动授权请求格式无效，请检查字段和资料大小",
                }
                raise HTTPException(422, detail, headers={"Cache-Control": "no-store"}) from None

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
                _automation_error_detail(response.content),
                headers={"Cache-Control": "no-store"},
            )
        return parse_response(response.content, model)

    async def view() -> AutomationView:
        return await call("GET", "", TypeAdapter(AutomationView))

    async def self_check() -> AutomationView:
        return await call("POST", "/self-check", TypeAdapter(AutomationView))

    async def login_script() -> LoginScriptStatus:
        return await call("GET", "/login-script", TypeAdapter(LoginScriptStatus))

    async def update_login_script(request: LoginScriptUpdate) -> LoginScriptStatus:
        package: Final = request.package
        body: Final = json.dumps({
            "publisher_key": request.publisher_key.get_secret_value(),
            "package": package.model_dump(mode="json") if package else None,
        }).encode()
        return await call("PUT", "/login-script", TypeAdapter(LoginScriptStatus), body)

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

    async def forget_login(profile_id: UUID) -> AutomationView:
        return await call("DELETE", f"/profiles/{profile_id}/login", TypeAdapter(AutomationView))

    async def release_reservation(profile_id: UUID) -> AutomationView:
        return await call("DELETE", f"/profiles/{profile_id}/reservation", TypeAdapter(AutomationView))

    async def submit(request: JobInput) -> JobView:
        return await call("POST", "/jobs", TypeAdapter(JobView), request.model_dump_json().encode())

    async def reauthorize(environment_id: UUID) -> JobView | None:
        return await call("POST", f"/environments/{environment_id}/reauthorize", TypeAdapter(JobView | None))

    async def delete_job(job_id: UUID) -> AutomationView:
        return await call("DELETE", f"/jobs/{job_id}", TypeAdapter(AutomationView))

    async def action(job_id: UUID, request: JobAction) -> JobView:
        return await call("POST", f"/jobs/{job_id}/action", TypeAdapter(JobView), request.model_dump_json().encode())

    async def screen(job_id: UUID) -> AutomationScreen:
        return await call("POST", f"/jobs/{job_id}/screen", TypeAdapter(AutomationScreen))

    router.add_api_route("", view, methods=["GET"])
    router.add_api_route("/self-check", self_check, methods=["POST"])
    router.add_api_route("/settings", settings, methods=["PUT"])
    router.add_api_route("/login-script", login_script, methods=["GET"])
    router.add_api_route("/login-script", update_login_script, methods=["PUT"])
    router.add_api_route("/profiles", create_profile, methods=["POST"], status_code=201)
    router.add_api_route("/profiles/{profile_id}", update_profile, methods=["PUT"])
    router.add_api_route("/profiles/{profile_id}", delete_profile, methods=["DELETE"])
    router.add_api_route("/profiles/{profile_id}/login", forget_login, methods=["DELETE"])
    router.add_api_route("/profiles/{profile_id}/reservation", release_reservation, methods=["DELETE"])
    router.add_api_route("/jobs", submit, methods=["POST"], status_code=202)
    router.add_api_route("/environments/{environment_id}/reauthorize", reauthorize, methods=["POST"], status_code=202)
    router.add_api_route("/jobs/{job_id}", delete_job, methods=["DELETE"])
    router.add_api_route("/jobs/{job_id}/action", action, methods=["POST"])
    router.add_api_route("/jobs/{job_id}/screen", screen, methods=["POST"])
    return router
