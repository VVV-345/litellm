"""本模块封装 Codebuff 授权请求与 FreeBuff 响应解析，代理客户端按请求注入，不管理 Docker。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict

from account_pool.config import validate_proxy_profile_url

_UPSTREAM_BASE_URL: Final = "https://www.codebuff.com"
_UPSTREAM_USER_AGENT: Final = "ai-sdk/openai-compatible/1.0.25/codebuff"


def proxy_http_client(proxy_url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=15.0, proxy=validate_proxy_profile_url(proxy_url), trust_env=False)


def _remaining_seconds(expires_at: str | None) -> int | None:
    """把上游 ISO 过期时间换算成剩余秒数；解析失败或已过期返回 None。"""
    if not expires_at:
        return None
    try:
        deadline: Final = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    remaining: Final = (deadline - datetime.now(timezone.utc)).total_seconds()
    return int(remaining) if remaining > 0 else None


@dataclass(frozen=True, slots=True)
class AuthorizationStart:
    authorization_url: str
    provider_state: str
    user_code: str | None
    expires_in_seconds: int | None


@dataclass(frozen=True, slots=True)
class CodeAuthorizationOperation:
    """codebuff 授权操作的完整凭据，state 与 hash 不落日志。"""

    authorization_url: str
    fingerprint_id: str
    fingerprint_hash: str
    expires_at: str
    expires_in_seconds: int | None = None


class _CodeStartResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    loginUrl: str
    fingerprintHash: str
    expiresAt: str | None = None


class _CodeStatusUser(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    authToken: str | None = None


class _CodeStatusResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    user: _CodeStatusUser | None = None


class _HealthResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    status: str
    accounts: int = 0
    alive_accounts: int = 0
    unknown_accounts: int = 0


class _ModelResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str


class _ModelsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    data: tuple[_ModelResponse, ...] = ()


class HttpCodebuffClient:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        proxy_client_factory: Callable[[str], httpx.AsyncClient] = proxy_http_client,
    ) -> None:
        self._client: Final = client or httpx.AsyncClient(timeout=15.0, trust_env=False)
        self._owns_client: Final = client is None
        self._proxy_client_factory: Final = proxy_client_factory

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @asynccontextmanager
    async def _client_for_proxy(self, proxy_url: str) -> AsyncIterator[httpx.AsyncClient]:
        if not proxy_url:
            yield self._client
            return
        # 每次读取账号当前代理，避免等待授权时切换出口仍复用旧连接。
        async with self._proxy_client_factory(proxy_url) as client:
            yield client

    async def start_authorization(self, fingerprint_id: str, *, proxy_url: str = "") -> CodeAuthorizationOperation:
        async with self._client_for_proxy(proxy_url) as client:
            response: Final = await client.post(
                f"{_UPSTREAM_BASE_URL}/api/auth/cli/code",
                headers={"User-Agent": _UPSTREAM_USER_AGENT},
                json={"fingerprintId": fingerprint_id},
            )
        response.raise_for_status()
        payload: Final = _CodeStartResponse.model_validate(response.json())
        return CodeAuthorizationOperation(
            authorization_url=payload.loginUrl,
            fingerprint_id=fingerprint_id,
            fingerprint_hash=payload.fingerprintHash,
            expires_at=payload.expiresAt or "",
            expires_in_seconds=_remaining_seconds(payload.expiresAt),
        )

    async def authorization_token(
        self,
        operation: CodeAuthorizationOperation,
        *,
        proxy_url: str = "",
    ) -> str | None:
        """返回 authToken；用户尚未完成授权时返回 None。"""
        async with self._client_for_proxy(proxy_url) as client:
            response: Final = await client.get(
                f"{_UPSTREAM_BASE_URL}/api/auth/cli/status",
                headers={"User-Agent": _UPSTREAM_USER_AGENT},
                params={
                    "fingerprintId": operation.fingerprint_id,
                    "fingerprintHash": operation.fingerprint_hash,
                    "expiresAt": operation.expires_at,
                },
            )
        if response.status_code == httpx.codes.UNAUTHORIZED:
            return None
        response.raise_for_status()
        payload: Final = _CodeStatusResponse.model_validate(response.json())
        token: Final = payload.user.authToken if payload.user is not None else None
        return token if token is not None and token.strip() else None
