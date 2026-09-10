"""本模块封装网关到 Manager 的内部协议，凭据只随服务间请求传递。"""

from __future__ import annotations

from typing import Final, Protocol

import httpx
from pydantic import BaseModel, TypeAdapter

from litellm.proxy.management_endpoints.account_pool_gateway_contracts import (
    AcquireRejected,
    AcquireRequest,
    FinishRequest,
    Lease,
    Resolution,
    ResolveRequest,
)

_ERROR_RESPONSE: Final = TypeAdapter(dict[str, object])


class ControlError(Exception):
    def __init__(self, status: int) -> None:
        self.status: Final = status
        super().__init__("Account pool control plane request failed")


class GatewayControl(Protocol):
    async def resolve(self, request: ResolveRequest) -> Resolution: ...
    async def acquire(self, request: AcquireRequest) -> Lease | AcquireRejected: ...
    async def finish(self, request: FinishRequest) -> None: ...


class ManagerControl:
    def __init__(self, client: httpx.AsyncClient, base_url: str, token: str) -> None:
        self.client: Final = client
        self.base_url: Final = base_url.rstrip("/")
        self.token: Final = token

    async def call(self, path: str, body: BaseModel) -> httpx.Response:
        response: Final = await self.client.post(
            f"{self.base_url}/api/internal/gateway/{path}",
            content=body.model_dump_json().encode(),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            timeout=10,
        )
        return response

    async def resolve(self, request: ResolveRequest) -> Resolution:
        response: Final = await self.call("resolve", request)
        if response.is_error:
            raise ControlError(response.status_code if response.status_code in (401, 403) else 503)
        return Resolution.model_validate_json(response.content)

    async def acquire(self, request: AcquireRequest) -> Lease | AcquireRejected:
        response: Final = await self.call("acquire", request)
        if response.status_code == 409:
            payload: Final = _ERROR_RESPONSE.validate_json(response.content)
            detail: Final = payload.get("detail")
            return AcquireRejected.model_validate(detail) if isinstance(detail, dict) else AcquireRejected(reason="configuration")
        if response.is_error:
            raise ControlError(response.status_code if response.status_code in (401, 403) else 503)
        return Lease.model_validate_json(response.content)

    async def finish(self, request: FinishRequest) -> None:
        response: Final = await self.call("finish", request)
        if response.is_error:
            raise ControlError(503)
