"""本模块管理 ISP 配置的导入、编辑、脱敏展示与到期状态。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime, timezone
from typing import Final
from uuid import UUID

from account_pool.isp_probe import ISPProbe
from account_pool.isp_subscription import MihomoISPNode, parse_isp_subscription
from account_pool.proxy_catalog import (
    ISPProbeResult,
    ISPProxyCatalogProfile,
    ISPProxyProfile,
    ISPProxyProfileView,
    ISPSubscriptionNodeProfile,
    ISPSubscriptionView,
    parse_isp_import,
)
from account_pool.proxy_catalog_repository import ProxyCatalogRepository


class ProxyCatalogService:
    def __init__(
        self, repository: ProxyCatalogRepository, reconcile: Callable[[date], Awaitable[None]] | None = None,
        probe: ISPProbe | None = None,
        fetch_subscription: Callable[[str], Awaitable[str]] | None = None,
    ) -> None:
        self._repository: Final = repository
        self._reconcile: Final = reconcile
        self._probe: Final = probe or ISPProbe()
        self._fetch_subscription: Final = fetch_subscription

    async def probe_isp(self, profile_id: UUID) -> ISPProbeResult:
        profile: Final = await self._repository.get_isp(profile_id)
        if profile is None:
            raise ValueError("ISP profile was not found")
        probe_profile: Final = (
            profile if isinstance(profile, ISPProxyProfile) else self._subscription_probe_profile(profile)
        )
        result: Final = (
            await self._probe.probe(probe_profile) if probe_profile is not None else ISPProbeResult(
                profile_id=profile.id, status="error", checked_at=datetime.now(timezone.utc),
                error="subscription_requires_gateway",
            )
        )
        return await self._repository.save_isp_probe(result)

    @staticmethod
    def _subscription_probe_profile(profile: ISPSubscriptionNodeProfile) -> ISPProxyProfile | None:
        config: Final = profile.config
        allowed: Final = {"name", "type", "server", "port", "username", "password", "udp", "tls"}
        if profile.proxy_type not in {"http", "socks5"} or set(config) - allowed:
            return None
        tls: Final = config.get("tls", False)
        host: Final = config.get("server")
        port: Final = config.get("port")
        username: Final = config.get("username", "")
        password: Final = config.get("password", "")
        if (
            type(tls) is not bool or (tls and profile.proxy_type != "http")
            or not isinstance(host, str) or type(port) is not int
            or not isinstance(username, str) or not isinstance(password, str)
        ):
            return None
        try:
            return ISPProxyProfile(
                id=profile.id, name=profile.name,
                protocol="https" if tls else "http" if profile.proxy_type == "http" else "socks5",
                host=host, port=port, username=username, password=password, enabled=profile.enabled,
            )
        except ValueError:
            return None

    async def list_isps(self) -> tuple[ISPProxyProfileView, ...]:
        profiles: Final = await self._repository.list_isps()
        bindings: Final = await self._repository.list_isp_bindings()
        probes: Final = {result.profile_id: result for result in await self._repository.list_isp_probes()}
        ports_by_profile: Final = {
            profile_id: tuple(port for port, bound_id in bindings if bound_id == profile_id)
            for profile_id in (profile.id for profile in profiles)
        }
        return tuple(
            self._profile_view(profile, ports_by_profile[profile.id], probes.get(profile.id))
            for profile in profiles
        )

    @staticmethod
    def _profile_view(
        profile: ISPProxyCatalogProfile,
        bound_ports: tuple[int, ...],
        last_probe: ISPProbeResult | None,
    ) -> ISPProxyProfileView:
        match profile:
            case ISPProxyProfile():
                return ISPProxyProfileView(
                    id=profile.id,
                    name=profile.name,
                    source="manual",
                    protocol=profile.protocol,
                    host=profile.host,
                    port=profile.port,
                    proxy_type=None,
                    expires_on=profile.expires_on,
                    enabled=profile.enabled and not profile.expired(date.today()),
                    credentials_configured=bool(profile.username or profile.password),
                    bound_ports=bound_ports,
                    last_probe=last_probe,
                )
            case ISPSubscriptionNodeProfile():
                return ISPProxyProfileView(
                    id=profile.id,
                    name=profile.name,
                    source="subscription",
                    protocol=None,
                    host=None,
                    port=None,
                    proxy_type=profile.proxy_type,
                    expires_on=None,
                    enabled=profile.enabled,
                    credentials_configured=False,
                    bound_ports=bound_ports,
                    last_probe=last_probe,
                )

    async def import_isps(self, source: str) -> tuple[ISPProxyProfileView, ...]:
        await self._repository.save_isps(parse_isp_import(source))
        return await self.list_isps()

    async def get_isp_subscription(self) -> ISPSubscriptionView:
        return await self._repository.get_isp_subscription_view()

    async def configure_isp_subscription(self, url: str) -> ISPSubscriptionView:
        return await self._refresh_isp_subscription(url.strip())

    async def refresh_isp_subscription(self) -> ISPSubscriptionView:
        url: Final = await self._repository.get_isp_subscription_url()
        if url is None:
            raise ValueError("ISP subscription is not configured")
        return await self._refresh_isp_subscription(url)

    async def _refresh_isp_subscription(self, url: str) -> ISPSubscriptionView:
        if not url:
            raise ValueError("ISP subscription URL is required")
        if self._fetch_subscription is None:
            raise ValueError("ISP subscription fetcher is not configured")
        content: Final = await self._fetch_subscription(url)
        nodes: Final[tuple[MihomoISPNode, ...]] = parse_isp_subscription(content)
        result: Final = await self._repository.save_isp_subscription(url, nodes)
        if self._reconcile is not None:
            await self._reconcile(date.today())
        return result

    async def update_isp(
        self,
        profile_id: UUID,
        *,
        name: str,
        protocol: str | None,
        host: str | None,
        port: int | None,
        username: str | None,
        password: str | None,
        clear_credentials: bool = False,
        expires_on: date | None,
        enabled: bool,
    ) -> ISPProxyProfileView:
        current: Final = await self._repository.get_isp(profile_id)
        if current is None:
            raise ValueError("ISP profile was not found")
        if isinstance(current, ISPSubscriptionNodeProfile):
            if (
                any(value is not None for value in (protocol, host, port, expires_on))
                or username or password or clear_credentials
            ):
                raise ValueError("subscription connection fields are provider-managed")
            updated_subscription: Final = ISPSubscriptionNodeProfile(
                id=current.id, name=name, proxy_type=current.proxy_type, config=current.config, enabled=enabled,
            )
            if await self._repository.update_isp(updated_subscription) is None:
                raise ValueError("ISP profile was not found")
            if self._reconcile is not None:
                await self._reconcile(date.today())
            return next(profile for profile in await self.list_isps() if profile.id == profile_id)
        if protocol is None or host is None or port is None:
            raise ValueError("manual ISP connection fields are required")
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
