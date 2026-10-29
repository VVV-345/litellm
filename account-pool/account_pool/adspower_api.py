"""提供管理员手动读取与同步 AdsPower 代理的接口，仅返回脱敏视图。"""

from collections.abc import Callable
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, Query

from account_pool.adspower import (
    AdsPowerError,
    AdsPowerProfilesView,
    AdsPowerSyncRequest,
    AdsPowerSyncResult,
    AdsPowerSyncService,
)


def create_adspower_router(service: AdsPowerSyncService, authorize: Callable[..., None]) -> APIRouter:
    router: Final = APIRouter(prefix="/api/adspower", dependencies=[Depends(authorize)])

    @router.get("/profiles")
    async def list_profiles(page: Annotated[int, Query(ge=1, le=10000)] = 1) -> AdsPowerProfilesView:
        try:
            return await service.list_profiles(page)
        except AdsPowerError as error:
            raise HTTPException(502, str(error)) from None

    @router.post("/sync")
    async def sync(request: AdsPowerSyncRequest) -> AdsPowerSyncResult:
        try:
            return await service.sync(request.profile_ids)
        except AdsPowerError as error:
            raise HTTPException(502, str(error)) from None

    return router
