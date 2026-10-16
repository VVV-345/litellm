"""本模块管理 ISP 配置的导入、编辑、脱敏展示与到期状态。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Final
from uuid import UUID

from account_pool.isp_probe import ISPProbe
from account_pool.proxy_catalog import ISPProbeResult, ISPProxyProfile, ISPProxyProfileView, parse_isp_import
from account_pool.proxy_catalog_repository import ProxyCatalogRepository


class ProxyCatalogService:
    def __init__(
        self, repository: ProxyCatalogRepository, reconcile: Callable[[date], Awaitable[None]] | None = None,
        probe: ISPProbe | None = None,
    ) -> None:
        self._repository: Final = repository
        self._reconcile: Final = reconcile
        self._probe: Final = probe or ISPProbe()

    async def probe_isp(self, profile_id: UUID) -> ISPProbeResult:
        profile: Final = await self._repository.get_isp(profile_id)
        if profile is None:
            raise ValueError("ISP profile was not found")
        result: Final = await self._probe.probe(profile)
        return await self._repository.save_isp_probe(result)

    async def list_isps(self) -> tuple[ISPProxyProfileView, ...]:
        profiles: Final = await self._repository.list_isps()
        bindings: Final = await self._repository.list_isp_bindings()
        probes: Final = {result.profile_id: result for result in await self._repository.list_isp_probes()}
        ports_by_profile: Final = {
            profile_id: tuple(port for port, bound_id in bindings if bound_id == profile_id)
            for profile_id in (profile.id for profile in profiles)
        }
        return tuple(
            ISPProxyProfileView(
                id=profile.id,
                name=profile.name,
                protocol=profile.protocol,
                host=profile.host,
                port=profile.port,
                expires_on=profile.expires_on,
                enabled=profile.enabled and not profile.expired(date.today()),
                credentials_configured=bool(profile.username or profile.password),
                bound_ports=ports_by_profile[profile.id],
                last_probe=probes.get(profile.id),
            )
            for profile in profiles
        )

    async def import_isps(self, source: str) -> tuple[ISPProxyProfileView, ...]:
        await self._repository.save_isps(parse_isp_import(source))
        return await self.list_isps()

    async def update_isp(
        self,
        profile_id: UUID,
        *,
        name: str,
        protocol: str,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        clear_credentials: bool = False,
        expires_on: date | None,
        enabled: bool,
    ) -> ISPProxyProfileView:
        current: Final = await self._repository.get_isp(profile_id)
        if current is None:
            raise ValueError("ISP profile was not found")
        updated: Final = ISPProxyProfile(
            id=profile_id,
            name=name,
            protocol=protocol,
            host=host,
            port=port,
            username="" if clear_credentials else username if username and username.strip() else current.username,
            password="" if clear_credentials else password if password and password.strip() else current.password,
            expires_on=expires_on,
            enabled=enabled,
        )
        if updated.enabled and updated.expired(date.today()):
            raise ValueError("expired ISP profiles cannot be enabled")
        persisted: Final = await self._repository.update_isp(updated)
        if persisted is None:
            raise ValueError("ISP profile was not found")
        if self._reconcile is not None:
            await self._reconcile(date.today())
        return next(profile for profile in await self.list_isps() if profile.id == profile_id)

    async def delete_isp(self, profile_id: UUID) -> None:
        if await self._repository.delete_isp(profile_id):
            return
        current: Final = await self._repository.get_isp(profile_id)
        if current is None:
            raise ValueError("ISP profile was not found")
        raise ValueError("ISP profile is assigned to a gateway port")

    async def deactivate_expired(self, today: date) -> tuple[UUID, ...]:
        return await self._repository.deactivate_expired(today)
