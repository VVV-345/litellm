"""读取 AdsPower 环境代理并导入加密 ISP 档案，不启动浏览器或改动现有出口。"""

from __future__ import annotations

import asyncio
from time import monotonic
from typing import Final, Literal, Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from account_pool.proxy_catalog import ISPProxyCatalogProfile, ISPProxyInput, ISPProxyProfile


class AdsPowerError(Exception):
    """只携带可公开的连接或导入错误。"""


class _Proxy(BaseModel):
    proxy_type: str = ""
    proxy_host: str = ""
    proxy_port: str | int = ""
    proxy_user: str = Field(default="", repr=False)
    proxy_password: str = Field(default="", repr=False)


class _Profile(BaseModel):
    user_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(default="", max_length=512)
    user_proxy_config: _Proxy | None = Field(default=None, repr=False)

    def proxy_input(self) -> ISPProxyInput | None:
        proxy: Final = self.user_proxy_config
        if proxy is None:
            return None
        protocol: Final = "socks5" if proxy.proxy_type == "socks5h" else proxy.proxy_type
        if protocol not in {"http", "https", "socks5"}:
            return None
        try:
            return ISPProxyInput.model_validate(
                {
                    "name": f"AdsPower [{self.user_id}] {self.name}"[:120],
                    "protocol": protocol,
                    "host": proxy.proxy_host,
                    "port": proxy.proxy_port,
                    "username": proxy.proxy_user,
                    "password": proxy.proxy_password,
                }
            )
        except ValidationError:
            return None

    def view(self) -> AdsPowerProfileView:
        proxy: Final = self.proxy_input()
        return AdsPowerProfileView(
            id=self.user_id,
            name=self.name or self.user_id,
            protocol=None if proxy is None else proxy.protocol,
            host=None if proxy is None else proxy.host,
            port=None if proxy is None else proxy.port,
            credentials_configured=bool(proxy and (proxy.username or proxy.password)),
            syncable=proxy is not None,
        )


class _Data(BaseModel):
    list: tuple[_Profile, ...] = ()


class _Response(BaseModel):
    code: int
    data: _Data | None = None
    msg: str = Field(default="", repr=False)


class AdsPowerProfileView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    name: str
    protocol: Literal["http", "https", "socks5"] | None
    host: str | None
    port: int | None
    credentials_configured: bool
    syncable: bool


class AdsPowerProfilesView(BaseModel):
    model_config = ConfigDict(frozen=True)
    page: int
    has_more: bool
    profiles: tuple[AdsPowerProfileView, ...]


class AdsPowerSyncRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    profile_ids: tuple[str, ...] = Field(min_length=1, max_length=20)


class AdsPowerSyncedProfile(BaseModel):
    model_config = ConfigDict(frozen=True)
    profile_id: str
    isp_profile_id: UUID
    name: str
    created: bool


class AdsPowerSyncResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    synced: tuple[AdsPowerSyncedProfile, ...]


class AdsPowerCatalog(Protocol):
    async def list_isps(self) -> tuple[ISPProxyCatalogProfile, ...]: ...
    async def save_isps(self, profiles: tuple[ISPProxyInput, ...]) -> tuple[ISPProxyProfile, ...]: ...


class AdsPowerClient:
    def __init__(self, base_url: str, api_key: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._base_url: Final = base_url.rstrip("/")
        self._api_key: Final = api_key
        self._client: Final = client or httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False)
        self._owns_client: Final = client is None
        self._request_lock: Final = asyncio.Lock()
        self._last_request: float = 0

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _profiles(self, params: dict[str, str]) -> tuple[_Profile, ...]:
        if not self._base_url or not self._api_key:
            raise AdsPowerError("AdsPower is not configured on the Manager")
        async with self._request_lock:
            first: Final = await self._paced_request(params)
            payload: Final = (
                await self._paced_request(params)
                if first.code != 0 and first.msg == "Too many request per second, please check"
                else first
            )
        if payload.code != 0 or payload.data is None:
            raise AdsPowerError("AdsPower rejected the request; check Local API access")
        return payload.data.list

    async def _paced_request(self, params: dict[str, str]) -> _Response:
        # Local API 每秒只接受一次请求；串行限速同时覆盖读取、批量同步和限流重试。
        await asyncio.sleep(max(0, 1.1 - (monotonic() - self._last_request)))
        self._last_request = monotonic()
        try:
            response: Final = await self._client.get(
                f"{self._base_url}/api/v1/user/list",
                params=params,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            response.raise_for_status()
            return _Response.model_validate_json(response.content)
        except (httpx.HTTPError, ValueError):
            raise AdsPowerError("AdsPower connection failed; check Local API and its API key") from None

    async def list_profiles(self, page: int) -> AdsPowerProfilesView:
        profiles: Final = await self._profiles({"page": str(page), "page_size": "100"})
        return AdsPowerProfilesView(
            page=page, has_more=len(profiles) == 100, profiles=tuple(p.view() for p in profiles)
        )

    async def proxy(self, profile_id: str) -> ISPProxyInput:
        profiles: Final = await self._profiles({"user_id": profile_id, "page": "1", "page_size": "100"})
        profile: Final = next((p for p in profiles if p.user_id == profile_id), None)
        proxy: Final = None if profile is None else profile.proxy_input()
        if proxy is None:
            raise AdsPowerError("Selected AdsPower profile has no supported HTTP or SOCKS5 proxy")
        return proxy


class AdsPowerSyncService:
    def __init__(self, client: AdsPowerClient, repository: AdsPowerCatalog) -> None:
        self._client: Final = client
        self._repository: Final = repository
        self._lock: Final = asyncio.Lock()

    async def list_profiles(self, page: int) -> AdsPowerProfilesView:
        return await self._client.list_profiles(page)

    async def sync(self, profile_ids: tuple[str, ...]) -> AdsPowerSyncResult:
        async with self._lock:
            identities: Final = tuple(dict.fromkeys(profile_ids))
            incoming: Final = tuple([await self._client.proxy(identity) for identity in identities])
            existing: Final = await self._repository.list_isps()
            matches: Final = tuple(
                next(
                    (
                        p
                        for p in existing
                        if isinstance(p, ISPProxyProfile)
                        and p.enabled
                        and p.name.startswith(f"AdsPower [{identity}] ")
                        and (p.protocol, p.host, p.port, p.username, p.password)
                        == (proxy.protocol, proxy.host, proxy.port, proxy.username, proxy.password)
                    ),
                    None,
                )
                for identity, proxy in zip(identities, incoming)
            )
            saved: Final = iter(
                await self._repository.save_isps(
                    tuple(proxy for proxy, match in zip(incoming, matches) if match is None)
                )
            )
            return AdsPowerSyncResult(
                synced=tuple(
                    AdsPowerSyncedProfile(
                        profile_id=identity, isp_profile_id=record.id, name=record.name, created=match is None
                    )
                    for identity, match in zip(identities, matches)
                    for record in (next(saved) if match is None else match,)
                )
            )
