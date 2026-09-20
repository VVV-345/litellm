"""CLIProxyAPI HTTP 传输，共用调用方客户端与密钥派生器，不单独持有连接生命周期。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Protocol, TypeAlias

import httpx

from account_pool.domain import (
    EnvironmentRecord,
)
from account_pool.shared.secrets import EnvironmentSecretDeriver, SecretPurpose

JSONValue: TypeAlias = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]


class ManagementRequest(Protocol):
    async def __call__(
        self,
        record: EnvironmentRecord,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: JSONValue | None = None,
        content: str | None = None,
        headers: Mapping[str, str] | None = None,
        management: bool = True,
        gateway: bool = False,
    ) -> httpx.Response: ...


@dataclass(frozen=True, slots=True)
class CLIProxyTransport:
    _client: httpx.AsyncClient
    _secrets: EnvironmentSecretDeriver

    async def request(
        self,
        record: EnvironmentRecord,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: JSONValue | None = None,
        content: str | None = None,
        headers: Mapping[str, str] | None = None,
        management: bool = True,
        gateway: bool = False,
    ) -> httpx.Response:
        host: Final = f"cliproxy-{record.id.hex}"
        auth_headers: Final = (
            {"X-Management-Key": self._secrets.derive(record.id, SecretPurpose.MANAGEMENT)}
            if management
            else {"Authorization": f"Bearer {self._secrets.derive(record.id, SecretPurpose.GATEWAY)}"}
            if gateway
            else None
        )
        request_headers: Final = {**(auth_headers or {}), **(headers or {})}
        response: Final = await self._client.request(
            method,
            f"http://{host}:8317{path}",
            headers=request_headers,
            params=params,
            json=json,
            content=content,
        )
        response.raise_for_status()
        return response

    async def request_multipart(
        self,
        record: EnvironmentRecord,
        method: str,
        path: str,
        filename: str,
        content: bytes,
        content_type: str | None,
        *,
        field_name: str = "files",
        fields: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        headers: Final = {"X-Management-Key": self._secrets.derive(record.id, SecretPurpose.MANAGEMENT)}
        return await self._client.request(
            method,
            f"http://cliproxy-{record.id.hex}:8317{path}",
            headers=headers,
            data=fields,
            files={field_name: (filename, content, content_type or "application/json")},
        )
