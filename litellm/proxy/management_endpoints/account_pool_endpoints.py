"""本模块为 Dashboard 代理固定的号池管理接口，浏览器不会接触 Manager 内部令牌。"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Annotated, Final, Literal, TypeVar
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter

from litellm._logging import verbose_proxy_logger
from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.common_utils.resource_ownership import is_proxy_admin
from litellm.proxy.management_endpoints.account_pool_reconciler import reconcile_configured_account_pool

_Method = Literal["DELETE", "GET", "POST", "PUT"]


class AccountPoolQuotaWindow(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    used_percent: float
    remaining_percent: float
    window_minutes: int
    resets_at: str | None = None


class AccountPoolQuotaSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    observed_at: str | None = None
    plan_type: str | None = None
    windows: tuple[AccountPoolQuotaWindow, ...] = ()


class AccountPoolModelQuotaSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    quota: AccountPoolQuotaSnapshot


class AccountPoolEnvironment(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    version: int = Field(ge=0)
    desired_state: str | None = None
    operation_id: str | None = None
    desired_configuration_version: int = Field(default=0, ge=0)
    observed_configuration_version: int = Field(default=0, ge=0)
    name: str
    provider: Literal["openai"]
    channel: Literal["cliproxyapi", "freebuff2api"] = "cliproxyapi"
    supplier: Literal[
        "openai_codex",
        "anthropic_claude",
        "google_antigravity",
        "kimi",
        "xai",
    ] = "openai_codex"
    configuration_pending: bool = False
    status: Literal[
        "provisioning",
        "awaiting_authorization",
        "validating",
        "ready",
        "cooling_down",
        "disabled",
        "error",
        "deleting",
    ]
    enabled: bool
    manual_cooldown: bool
    concurrency_limit: int
    proxy_mode: Literal["default_gateway", "profile"]
    proxy_profile_id: str | None
    available_models: tuple[str, ...]
    enabled_models: tuple[str, ...]
    quota: AccountPoolQuotaSnapshot
    model_quotas: tuple[AccountPoolModelQuotaSnapshot, ...] = ()
    cooldown_until: str | None
    automatic_cooldown: bool = False
    last_error: str | None
    created_at: str
    updated_at: str


class AccountPoolCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=80)
    provider: Literal["openai"] = "openai"
    channel: Literal["cliproxyapi", "freebuff2api"] = "cliproxyapi"
    supplier: Literal[
        "openai_codex",
        "anthropic_claude",
        "google_antigravity",
        "kimi",
        "xai",
    ] = "openai_codex"


class AccountPoolUpdateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int = Field(ge=0)
    operation_id: str | None = Field(default=None, max_length=160)
    name: str = Field(min_length=1, max_length=80)
    concurrency_limit: int = Field(ge=1, le=1000)
    enabled: bool
    manual_cooldown: bool
    proxy_mode: Literal["default_gateway", "profile"]
    proxy_profile_id: str | None = Field(default=None, max_length=120)
    enabled_models: tuple[str, ...]


class AccountPoolAuthorization(BaseModel):
    model_config = ConfigDict(frozen=True)

    environment: AccountPoolEnvironment
    flow: Literal["browser_oauth", "device_code"]
    authorization_url: HttpUrl
    ssh_command: str | None
    user_code: str | None
    expires_at: str


class AccountPoolProxyProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    protocol: str | None = None


_ENVIRONMENTS: Final = TypeAdapter(tuple[AccountPoolEnvironment, ...])
_ENVIRONMENT: Final = TypeAdapter(AccountPoolEnvironment)
_AUTHORIZATION: Final = TypeAdapter(AccountPoolAuthorization)
_PROFILES: Final = TypeAdapter(tuple[AccountPoolProxyProfile, ...])


class AccountPoolManagerClient:
    def __init__(
        self,
        base_url: str,
        token: str | None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url: Final = base_url.rstrip("/")
        self._token: Final = token
        self._client: Final = client or httpx.AsyncClient(timeout=30.0, trust_env=False)
        self._owns_client: Final = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def request(
        self,
        method: _Method,
        path: str,
        body: bytes | None = None,
        idempotency_key: str | None = None,
    ) -> httpx.Response:
        if self._token is None or len(self._token) < 32:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Account Pool Manager is not configured",
            )
        try:
            headers: Final = {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                **({"Idempotency-Key": idempotency_key} if idempotency_key is not None else {}),
            }
            return await self._client.request(
                method,
                f"{self._base_url}{path}",
                headers=headers,
                content=body,
            )
        except httpx.HTTPError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Account Pool Manager is unavailable",
            ) from error


ManagerClientFactory = Callable[[], AccountPoolManagerClient]


async def _manager_request(
    client_factory: ManagerClientFactory,
    method: _Method,
    path: str,
    body: bytes | None = None,
    idempotency_key: str | None = None,
) -> httpx.Response:
    client: Final = client_factory()
    try:
        return await client.request(method, path, body, idempotency_key)
    finally:
        await client.close()


def _default_client() -> AccountPoolManagerClient:
    return AccountPoolManagerClient(
        base_url=os.getenv("ACCOUNT_POOL_MANAGER_URL", "http://account-pool:8091"),
        token=os.getenv("ACCOUNT_POOL_MANAGER_TOKEN"),
    )


def create_account_pool_router(client_factory: ManagerClientFactory = _default_client) -> APIRouter:
    router: Final = APIRouter(prefix="/account_pool", tags=["Account Pool"])

    @router.get("/environments", response_model=tuple[AccountPoolEnvironment, ...])
    async def list_environments(
        user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
    ) -> tuple[AccountPoolEnvironment, ...]:
        _require_proxy_admin(user_api_key_dict)
        response: Final = await _manager_request(client_factory, "GET", "/api/environments")
        return _validate_response(response, _ENVIRONMENTS)

    @router.post("/environments", response_model=AccountPoolAuthorization)
    async def create_environment(
        request: AccountPoolCreateRequest,
        user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=160)] = None,
    ) -> AccountPoolAuthorization:
        _require_proxy_admin(user_api_key_dict)
        response: Final = await _manager_request(
            client_factory,
            "POST",
            "/api/environments",
            request.model_dump_json().encode("utf-8"),
            idempotency_key,
        )
        return _validate_response(response, _AUTHORIZATION)

    @router.get("/environments/{environment_id}", response_model=AccountPoolEnvironment)
    async def get_environment(
        environment_id: UUID,
        user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
    ) -> AccountPoolEnvironment:
        _require_proxy_admin(user_api_key_dict)
        response: Final = await _manager_request(client_factory, "GET", f"/api/environments/{environment_id}")
        return _validate_response(response, _ENVIRONMENT)

    @router.put("/environments/{environment_id}", response_model=AccountPoolEnvironment)
    async def update_environment(
        environment_id: UUID,
        request: AccountPoolUpdateRequest,
        user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=160)] = None,
    ) -> AccountPoolEnvironment:
        _require_proxy_admin(user_api_key_dict)
        response: Final = await _manager_request(
            client_factory,
            "PUT",
            f"/api/environments/{environment_id}",
            request.model_dump_json().encode("utf-8"),
            idempotency_key,
        )
        environment: Final = _validate_response(response, _ENVIRONMENT)
        await _reconcile_after_saved_change()
        return environment

    @router.post("/environments/{environment_id}/authorize", response_model=AccountPoolAuthorization)
    async def authorize_environment(
        environment_id: UUID,
        user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=160)] = None,
    ) -> AccountPoolAuthorization:
        _require_proxy_admin(user_api_key_dict)
        path: Final = f"/api/environments/{environment_id}/authorize"
        response: Final = await _manager_request(client_factory, "POST", path, idempotency_key=idempotency_key)
        authorization: Final = _validate_response(response, _AUTHORIZATION)
        return authorization

    @router.delete("/environments/{environment_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_environment(
        environment_id: UUID,
        user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=160)] = None,
    ) -> None:
        _require_proxy_admin(user_api_key_dict)
        response: Final = await _manager_request(
            client_factory,
            "DELETE",
            f"/api/environments/{environment_id}",
            idempotency_key=idempotency_key,
        )
        _raise_for_upstream_error(response)
        await _reconcile_after_saved_change()

    @router.get("/proxy-profiles", response_model=tuple[AccountPoolProxyProfile, ...])
    async def list_proxy_profiles(
        user_api_key_dict: Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)],
    ) -> tuple[AccountPoolProxyProfile, ...]:
        _require_proxy_admin(user_api_key_dict)
        response: Final = await _manager_request(client_factory, "GET", "/api/proxy-profiles")
        return _validate_response(response, _PROFILES)

    return router


def _require_proxy_admin(user_api_key_dict: UserAPIKeyAuth) -> None:
    if not is_proxy_admin(user_api_key_dict):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only proxy admins can manage the account pool"
        )


def _raise_for_upstream_error(response: httpx.Response) -> None:
    if response.is_error:
        raise HTTPException(status_code=response.status_code, detail=_upstream_error(response))


T = TypeVar("T")


def _validate_response(response: httpx.Response, adapter: TypeAdapter[T]) -> T:
    if response.is_error:
        detail: Final = _upstream_error(response)
        raise HTTPException(status_code=response.status_code, detail=detail)
    try:
        return adapter.validate_json(response.content)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Account Pool Manager returned an invalid response",
        ) from error


def _upstream_error(response: httpx.Response) -> str:
    try:
        payload: Final = TypeAdapter(dict[str, object]).validate_json(response.content)
    except ValueError:
        return "Account Pool Manager request failed"
    detail: Final = payload.get("detail")
    return detail if isinstance(detail, str) else "Account Pool Manager request failed"


async def _reconcile_after_saved_change() -> None:
    try:
        await reconcile_configured_account_pool()
    except Exception as error:
        verbose_proxy_logger.warning("Account pool settings saved, but deployment reconciliation failed: %s", error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Settings were saved, but gateway routing is still retrying synchronization",
        ) from error


router: Final = create_account_pool_router()
