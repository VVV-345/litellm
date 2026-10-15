"""通过已保存的 ISP 检测出口和近似位置，不修改代理配置或返回凭据。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from ipaddress import ip_address
from time import perf_counter
from typing import Final, Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress

from account_pool.proxy_catalog import ISPProxyProfile


class ISPProbeResult(BaseModel):
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


class _ExitIP(BaseModel):
    ip: IPvAnyAddress


class _Connection(BaseModel):
    isp: str | None = Field(default=None, max_length=200)


class _Location(BaseModel):
    success: bool
    ip: IPvAnyAddress
    country: str | None = Field(default=None, max_length=200)
    region: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=200)
    connection: _Connection = Field(default_factory=_Connection)


def _client(proxy: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(proxy=proxy, timeout=6.0, trust_env=False, follow_redirects=False)


class ISPProbe:
    def __init__(
        self,
        client_factory: Callable[[str], AbstractAsyncContextManager[httpx.AsyncClient]] = _client,
    ) -> None:
        self._client_factory: Final = client_factory
        self._slots: Final = asyncio.Semaphore(3)

    async def probe(self, profile: ISPProxyProfile) -> ISPProbeResult:
        checked_at: Final = datetime.now(timezone.utc)
        try:
            async with asyncio.timeout(12), self._slots:
                return await self._probe(profile, checked_at)
        except (httpx.TimeoutException, TimeoutError):
            return ISPProbeResult(profile_id=profile.id, status="timeout", checked_at=checked_at)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):
            return ISPProbeResult(profile_id=profile.id, status="error", checked_at=checked_at)

    async def _probe(self, profile: ISPProxyProfile, checked_at: datetime) -> ISPProbeResult:
        proxy: Final = str(httpx.URL(
            scheme=profile.protocol, host=profile.host, port=profile.port,
            username=profile.username, password=profile.password,
        ))
        async with self._client_factory(proxy) as client:
            started: Final = perf_counter()
            response: Final = await client.get("https://api.ipify.org?format=json")
            response.raise_for_status()
            address: Final = str(_ExitIP.model_validate_json(response.content).ip)
            if not ip_address(address).is_global:
                raise ValueError("invalid exit IP")
            result: Final = ISPProbeResult(
                profile_id=profile.id, status="ok", checked_at=checked_at, exit_ip=address,
                delay_ms=max(0, round((perf_counter() - started) * 1000)),
            )
            try:
                async with asyncio.timeout(4):
                    location_response: Final = await client.get(f"https://ipwho.is/{address}")
                    location_response.raise_for_status()
                    location: Final = _Location.model_validate_json(location_response.content)
                    if not location.success or str(location.ip) != address:
                        return result
                    return result.model_copy(update={
                        "country": location.country, "region": location.region, "city": location.city,
                        "isp": location.connection.isp,
                    })
            except (httpx.HTTPError, ValueError, TimeoutError):
                return result
