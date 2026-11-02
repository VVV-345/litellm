"""暴露单卡重置与查询的管理接口，显式确认兑换且不返回凭据绑定信息。"""

from collections.abc import Callable
from typing import Final
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException

from account_pool.quota_reset import QuotaResetService
from account_pool.quota_reset_models import (
    ConsumeResetRequest,
    PrepareResetRequest,
    ResetOperation,
    ResetStatus,
    ResetVerification,
    ResetView,
    VerifyResetRequest,
)


def reset_view(operation: ResetOperation) -> ResetView:
    return ResetView.model_validate(operation.model_dump(exclude={"credential_fingerprints"}))


def create_quota_reset_router(service: QuotaResetService, require_manager: Callable[[], None]) -> APIRouter:
    router: Final = APIRouter(
        prefix="/api/environments/{environment_id}/quota-reset",
        dependencies=[Depends(require_manager)],
    )

    async def status(environment_id: UUID) -> ResetStatus:
        try:
            record: Final = await service.record(environment_id)
            operation: Final = await service.store.latest(environment_id)
            verification = await service.store.latest_verification(environment_id)
            try:
                observation: Final = await service.upstream.observe(record)
            except (ValueError, httpx.HTTPError, TimeoutError):
                return ResetStatus(observation=None, operation=None if operation is None else reset_view(operation), last_verification=verification)
            return ResetStatus(observation=observation, operation=None if operation is None else reset_view(operation), last_verification=verification)
        except (ValueError, httpx.HTTPError, TimeoutError):
            raise HTTPException(409, "无法查询，请核对卡片凭据、出站连接与供应商状态") from None

    async def prepare(environment_id: UUID, request: PrepareResetRequest) -> ResetView:
        try:
            return reset_view(await service.prepare(environment_id, request.previous_request_id))
        except (ValueError, httpx.HTTPError, TimeoutError):
            raise HTTPException(409, "无法准备兑换，请核对卡片绑定、适用次数及额度状态") from None

    async def consume(environment_id: UUID, request: ConsumeResetRequest) -> ResetView:
        try:
            return reset_view(await service.consume(environment_id, request.request_id))
        except (ValueError, httpx.HTTPError, TimeoutError):
            raise HTTPException(409, "兑换未完成，请查询原操作；不要创建新的兑换身份") from None

    async def verify(environment_id: UUID, request: VerifyResetRequest) -> ResetVerification:
        try:
            return await service.verify(environment_id, request.model, request.recover)
        except (ValueError, httpx.HTTPError, TimeoutError):
            raise HTTPException(409, "调用验证未完成，未确认号池恢复") from None

    router.add_api_route("", status, methods=["GET"], response_model=ResetStatus)
    router.add_api_route("/prepare", prepare, methods=["POST"], response_model=ResetView)
    router.add_api_route("/consume", consume, methods=["POST"], response_model=ResetView)
    router.add_api_route("/verify", verify, methods=["POST"], response_model=ResetVerification)
    return router
