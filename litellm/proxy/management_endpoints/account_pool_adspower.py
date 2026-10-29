"""转发 AdsPower 手动代理同步请求，复用管理员权限且不接受或返回代理密码。"""

from collections.abc import Callable
from typing import Annotated, Final, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_management import ManagementRequest, parse_response


class AccountPoolAdsPowerProfile(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    name: str
    protocol: Literal["http", "https", "socks5"] | None
    host: str | None
    port: int | None
    credentials_configured: bool
    syncable: bool


class AccountPoolAdsPowerProfiles(BaseModel):
    model_config = ConfigDict(frozen=True)
    page: int
    has_more: bool
    profiles: tuple[AccountPoolAdsPowerProfile, ...]


class AccountPoolAdsPowerSyncRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    profile_ids: tuple[str, ...] = Field(min_length=1, max_length=20)


class AccountPoolAdsPowerSyncedProfile(BaseModel):
    model_config = ConfigDict(frozen=True)
    profile_id: str
    isp_profile_id: UUID
    name: str
    created: bool


class AccountPoolAdsPowerSyncResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    synced: tuple[AccountPoolAdsPowerSyncedProfile, ...]


def create_adspower_router(
    request_manager: ManagementRequest,
    require_admin: Callable[[UserAPIKeyAuth], None],
) -> APIRouter:
    def authorize(user: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)]) -> None:
        require_admin(user)

    router: Final = APIRouter(prefix="/adspower", dependencies=[Depends(authorize)])

    @router.get("/profiles")
    async def list_profiles(page: Annotated[int, Query(ge=1, le=10000)] = 1) -> AccountPoolAdsPowerProfiles:
        response: Final = await request_manager("GET", f"/api/adspower/profiles?page={page}", None)
        if response.is_error:
            raise HTTPException(response.status_code, "AdsPower is unavailable; check Manager Local API configuration")
        return parse_response(response.content, TypeAdapter(AccountPoolAdsPowerProfiles))

    @router.post("/sync")
    async def sync(request: AccountPoolAdsPowerSyncRequest) -> AccountPoolAdsPowerSyncResult:
        response: Final = await request_manager("POST", "/api/adspower/sync", request.model_dump_json().encode())
        if response.is_error:
            raise HTTPException(response.status_code, "AdsPower sync failed; check the selected profiles and Local API")
        return parse_response(response.content, TypeAdapter(AccountPoolAdsPowerSyncResult))

    return router
