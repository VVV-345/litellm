"""本模块转发 ISP 与代理配置档案管理接口，复用管理员鉴权并只返回脱敏视图。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Final, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_management import ManagementRequest, parse_response


class AccountPoolISPProxy(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    name: str
    protocol: Literal["http", "https", "socks5"]
    host: str
    port: int
    expires_on: date | None
    enabled: bool
    bound_ports: tuple[int, ...] = ()


class AccountPoolISPImport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    content: str = Field(min_length=1, max_length=2_097_152, repr=False)


class AccountPoolISPUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    protocol: Literal["http", "https", "socks5"]
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    username: str | None = Field(default=None, max_length=512, repr=False)
    password: str | None = Field(default=None, max_length=2048, repr=False)
    expires_on: date | None = None
    enabled: bool


class AccountPoolProxyConfigurationProfile(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    name: str
    created_at: datetime
    active: bool


class AccountPoolProxyConfigurationSave(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    content: str | None = Field(default=None, max_length=2_097_152, repr=False)
    url: str | None = Field(default=None, max_length=2048, repr=False)


def create_proxy_catalog_router(
    request_manager: ManagementRequest, require_admin: Callable[[UserAPIKeyAuth], None]
) -> APIRouter:
    def authorize(user: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)]) -> None:
        require_admin(user)

    router: Final = APIRouter(dependencies=[Depends(authorize)])

    async def call(method: Literal["GET", "POST", "PUT", "DELETE"], path: str, body: bytes | None = None) -> bytes:
        response: Final = await request_manager(method, "/api" + path, body)
        if response.is_error:
            raise HTTPException(response.status_code, "Proxy catalog operation failed; refresh and retry")
        return response.content

    @router.get("/proxy-isps")
    async def list_isps() -> tuple[AccountPoolISPProxy, ...]:
        return parse_response(await call("GET", '/proxy-isps'), TypeAdapter(tuple[AccountPoolISPProxy, ...]))

    @router.post("/proxy-isps/import", status_code=201)
    async def import_isps(request: AccountPoolISPImport) -> tuple[AccountPoolISPProxy, ...]:
        return parse_response(await call("POST", '/proxy-isps/import', request.model_dump_json().encode()), TypeAdapter(tuple[AccountPoolISPProxy, ...]))

    @router.put("/proxy-isps/{profile_id}")
    async def update_isp(profile_id: UUID, request: AccountPoolISPUpdate) -> AccountPoolISPProxy:
        return parse_response(await call("PUT", f'/proxy-isps/{profile_id}', request.model_dump_json().encode()), TypeAdapter(AccountPoolISPProxy))

    @router.delete("/proxy-isps/{profile_id}", status_code=204)
    async def delete_isp(profile_id: UUID) -> None:
        await call("DELETE", f'/proxy-isps/{profile_id}')

    @router.get("/proxy-gateways/configurations")
    async def list_configurations() -> tuple[AccountPoolProxyConfigurationProfile, ...]:
        return parse_response(await call("GET", '/proxy-gateways/configurations'), TypeAdapter(tuple[AccountPoolProxyConfigurationProfile, ...]))

    @router.post("/proxy-gateways/configurations", status_code=201)
    async def save_configuration(request: AccountPoolProxyConfigurationSave) -> AccountPoolProxyConfigurationProfile:
        return parse_response(await call("POST", '/proxy-gateways/configurations', request.model_dump_json().encode()), TypeAdapter(AccountPoolProxyConfigurationProfile))

    @router.put("/proxy-gateways/configurations/{profile_id}/activate")
    async def activate_configuration(profile_id: UUID) -> AccountPoolProxyConfigurationProfile:
        return parse_response(await call("PUT", f'/proxy-gateways/configurations/{profile_id}/activate'), TypeAdapter(AccountPoolProxyConfigurationProfile))

    @router.delete("/proxy-gateways/configurations/{profile_id}", status_code=204)
    async def delete_configuration(profile_id: UUID) -> None:
        await call("DELETE", f'/proxy-gateways/configurations/{profile_id}')

    return router
