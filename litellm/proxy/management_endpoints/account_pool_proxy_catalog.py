"""本模块转发 ISP 与代理配置档案管理接口，复用管理员鉴权并只返回脱敏视图。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Annotated, Final, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_management import ManagementRequest, parse_response


class ProxyCatalogRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler: Final = super().get_route_handler()

        async def validated(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, "Invalid proxy catalog request; check fields and import size") from None

        return validated


class AccountPoolISPImport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    content: str = Field(min_length=1, max_length=2_097_152, repr=False)


class AccountPoolISPProbe(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile_id: UUID
    status: Literal["ok", "timeout", "error"]
    checked_at: datetime
    exit_ip: str | None = None
    delay_ms: int | None = None
    country: str | None = None
    region: str | None = None
    city: str | None = None
    isp: str | None = None
    error: str | None = None


class AccountPoolISPProxy(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    name: str
    source: Literal["manual", "subscription"] = "manual"
    protocol: Literal["http", "https", "socks5"] | None = None
    host: str | None = None
    port: int | None = None
    proxy_type: str | None = None
    expires_on: date | None = None
    enabled: bool
    credentials_configured: bool
    bound_ports: tuple[int, ...] = ()
    last_probe: AccountPoolISPProbe | None = None


class AccountPoolISPSubscription(BaseModel):
    model_config = ConfigDict(frozen=True)

    configured: bool
    refreshed_at: datetime | None = None
    node_count: int = Field(ge=0)
    enabled_node_count: int = Field(ge=0)


class AccountPoolISPSubscriptionConfigure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str = Field(min_length=1, max_length=2048, repr=False)


class AccountPoolISPUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    protocol: Literal["http", "https", "socks5"] | None = None
    host: str | None = Field(default=None, min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=512, repr=False)
    password: str | None = Field(default=None, max_length=2048, repr=False)
    clear_credentials: bool = False
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

    router: Final = APIRouter(dependencies=[Depends(authorize)], route_class=ProxyCatalogRoute)

    async def call(method: Literal["GET", "POST", "PUT", "DELETE"], path: str, body: bytes | None = None) -> bytes:
        response: Final = await request_manager(method, "/api" + path, body)
        if response.is_error:
            raise HTTPException(response.status_code, "Proxy catalog operation failed; refresh and retry")
        return response.content

    @router.get("/proxy-isps")
    async def list_isps() -> tuple[AccountPoolISPProxy, ...]:
        return parse_response(await call("GET", '/proxy-isps'), TypeAdapter(tuple[AccountPoolISPProxy, ...]))

    @router.get("/proxy-isps/subscription")
    async def get_isp_subscription() -> AccountPoolISPSubscription:
        return parse_response(
            await call("GET", "/proxy-isps/subscription"), TypeAdapter(AccountPoolISPSubscription)
        )

    @router.put("/proxy-isps/subscription")
    async def configure_isp_subscription(request: AccountPoolISPSubscriptionConfigure) -> AccountPoolISPSubscription:
        return parse_response(
            await call("PUT", "/proxy-isps/subscription", request.model_dump_json().encode()),
            TypeAdapter(AccountPoolISPSubscription),
        )

    @router.post("/proxy-isps/subscription/refresh")
    async def refresh_isp_subscription() -> AccountPoolISPSubscription:
        return parse_response(
            await call("POST", "/proxy-isps/subscription/refresh"), TypeAdapter(AccountPoolISPSubscription)
        )

    async def probe_isp(profile_id: UUID) -> AccountPoolISPProbe:
        return parse_response(
            await call("POST", f"/proxy-isps/{profile_id}/probe"), TypeAdapter(AccountPoolISPProbe)
        )

    router.add_api_route("/proxy-isps/{profile_id}/probe", probe_isp, methods=["POST"])

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
