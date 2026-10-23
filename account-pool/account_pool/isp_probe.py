"""通过已保存的 ISP 检测出口和近似位置，不修改代理配置或返回凭据。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from ipaddress import ip_address
from time import perf_counter
from typing import Final, Literal

import httpx
from pydantic import BaseModel, Field, IPvAnyAddress

from account_pool.proxy_catalog import ISPProbeResult, ISPProxyProfile

_MAX_PROBE_RESPONSE_BYTES: Final = 64 * 1024
_OPENAI_MODELS_URL: Final = "https://api.openai.com/v1/models"
_EXIT_IP_URL: Final = "https://api.ipify.org?format=json"


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


class GatewayISPProbeResult(BaseModel):
    model_config = {"frozen": True}

    port: int = Field(ge=1, le=65535)
    checked_at: datetime
    ip_status: Literal["ok", "timeout", "error"]
    exit_ip: str | None = None
    country: str | None = None
    region: str | None = None
    city: str | None = None
    isp: str | None = None
    openai_status: Literal["response", "timeout", "error"]
    openai_http_status: int | None = Field(default=None, ge=100, le=599)


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
            return ISPProbeResult(profile_id=profile.id, status="timeout", checked_at=checked_at, error="timeout")
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):
            return ISPProbeResult(profile_id=profile.id, status="error", checked_at=checked_at, error="probe_failed")

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


class GatewayISPProbe:
    def __init__(
        self,
        client_factory: Callable[[str], AbstractAsyncContextManager[httpx.AsyncClient]] = _client,
    ) -> None:
        self._client_factory: Final = client_factory
        self._slots: Final = asyncio.Semaphore(3)

    async def probe(self, port: int, proxy_url: str) -> GatewayISPProbeResult:
        checked_at: Final = datetime.now(timezone.utc)
        ip_status: Literal["ok", "timeout", "error"] = "error"
        exit_ip: str | None = None
        country: str | None = None
        region: str | None = None
        city: str | None = None
        isp: str | None = None
        openai_status: Literal["response", "timeout", "error"] = "error"
        openai_http_status: int | None = None
        try:
            async with asyncio.timeout(18), self._slots:
                async with self._client_factory(proxy_url) as client:
                    try:
                        body: Final = await self._read_limited(client, _EXIT_IP_URL)
                        address: Final = str(_ExitIP.model_validate_json(body).ip)
                        if not ip_address(address).is_global:
                            raise ValueError("exit IP is not public")
                        ip_status = "ok"
                        exit_ip = address
                        try:
                            location_body: Final = await self._read_limited(client, f"https://ipwho.is/{address}")
                            location: Final = _Location.model_validate_json(location_body)
                            if location.success and str(location.ip) == address:
                                country = location.country
                                region = location.region
                                city = location.city
                                isp = location.connection.isp
                        except (httpx.HTTPError, ValueError, TimeoutError):
                            pass
                    except (httpx.TimeoutException, TimeoutError):
                        ip_status = "timeout"
                    except (httpx.HTTPError, ValueError):
                        ip_status = "error"
                    try:
                        async with client.stream("GET", _OPENAI_MODELS_URL) as response:
                            openai_http_status = response.status_code
                        openai_status = "response"
                    except (httpx.TimeoutException, TimeoutError):
                        openai_status = "timeout"
                    except httpx.HTTPError:
                        openai_status = "error"
        except (httpx.TimeoutException, TimeoutError):
            ip_status = "timeout"
            openai_status = "timeout"
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):
            ip_status = "error"
            openai_status = "error"
        return GatewayISPProbeResult(
            port=port,
            checked_at=checked_at,
            ip_status=ip_status,
            exit_ip=exit_ip,
            country=country,
            region=region,
            city=city,
            isp=isp,
            openai_status=openai_status,
            openai_http_status=openai_http_status,
        )

    @staticmethod
    async def _read_limited(client: httpx.AsyncClient, url: str) -> bytes:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            body: bytes = b""
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > _MAX_PROBE_RESPONSE_BYTES:
                    raise ValueError("probe response is too large")
                body += chunk
        return body
