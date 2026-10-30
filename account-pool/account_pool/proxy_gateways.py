"""本模块编排 Clash 代理网关：把各端口登记为代理名单条目、查询状态、切换出口节点。"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import tempfile
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Final, cast
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import yaml

from account_pool.clash import ClashController, ClashDelayResult, ClashDelayStatus, ClashError, ClashProxyNode
from account_pool.config import Settings
from account_pool.isp_probe import GatewayISPProbe, GatewayISPProbeResult
from account_pool.ports import ProxyProfileRepository
from account_pool.proxy_catalog import (
    ISPProxyCatalogProfile,
    ISPProxyProfile,
    ISPSubscriptionNodeProfile,
    ProxyConfigurationProfileView,
)
from account_pool.proxy_catalog_repository import ProxyCatalogRepository

_GATEWAY_PROFILE_PREFIX: Final = "clash-gateway-"
_LOGGER: Final = logging.getLogger(__name__)
_MAX_CONFIGURATION_BYTES: Final = 2 * 1024 * 1024
_MAX_CONFIGURATION_URL_LENGTH: Final = 2048
_NON_EGRESS_PROXY_TYPES: Final = frozenset(
    {"compatible", "direct", "fallback", "loadbalance", "pass", "reject", "relay", "selector", "urltest"}
)
_NON_EGRESS_PROXY_NAMES: Final = frozenset({"DIRECT", "REJECT"})


@dataclass(frozen=True, slots=True)
class GatewayView:
    port: int
    profile_id: str
    name: str
    proxy_url: str
    current_node: str | None
    isp_profile_id: UUID | None = None
    front_node_name: str | None = None


@dataclass(frozen=True, slots=True)
class GatewayConfigurationView:
    config_path: str | None


@dataclass(frozen=True, slots=True)
class GatewayDelayView:
    port: int
    current_node: str | None
    status: ClashDelayStatus
    delay_ms: int | None
    checked_at: datetime
    gateway_status: ClashDelayStatus
    gateway_delay_ms: int | None


class ProxyGatewayService:
    """对外提供代理网关管理；账号侧继续消费既有 ProxyProfileRepository，互不感知。"""

    def __init__(
        self,
        settings: Settings,
        profiles: ProxyProfileRepository,
        controller: ClashController,
        *,
        fetch_configuration: Callable[[str], Awaitable[str]] | None = None,
        catalog: ProxyCatalogRepository | None = None,
        gateway_probe: GatewayISPProbe | None = None,
    ) -> None:
        self._settings: Final = settings
        self._profiles: Final = profiles
        self._controller: Final = controller
        self._delay_slots: Final = asyncio.Semaphore(10)
        self._registry_lock: Final = asyncio.Lock()
        self._profile_locks: Final[dict[str, asyncio.Lock]] = {}
        self._dynamic_registry: Final = callable(getattr(profiles, "delete_gateway", None))
        self._fetch_configuration: Final = fetch_configuration or self._download_configuration
        self._catalog: Final = catalog
        self._gateway_probe: Final = gateway_probe or GatewayISPProbe()

    @asynccontextmanager
    async def profile_operation(self, profile_id: str | None) -> AsyncGenerator[None, None]:
        if profile_id is None or not profile_id.startswith(_GATEWAY_PROFILE_PREFIX):
            yield
            return
        # 同一出口的授权启动与节点切换互斥；不同出口不等待，也不持锁等待人工登录。
        lock: Final = self._profile_locks.setdefault(profile_id, asyncio.Lock())
        async with lock:
            yield

    @classmethod
    def disabled(
        cls,
        settings: Settings,
        profiles: ProxyProfileRepository,
        *,
        catalog: ProxyCatalogRepository | None = None,
    ) -> ProxyGatewayService:
        """Clash 未配置时的占位实例：列表为空，调用切换即报错。"""

        class _InactiveController:
            async def list_nodes(self) -> tuple[ClashProxyNode, ...]:
                return ()

            async def selector_current(self, selector_name: str) -> str | None:
                raise ClashError("clash controller is not configured")

            async def selector_currents(self, selector_names: tuple[str, ...]) -> tuple[str | None, ...]:
                raise ClashError("clash controller is not configured")

            async def switch_selector(self, selector_name: str, node_name: str) -> None:
                raise ClashError("clash controller is not configured")

            async def reload_config(self, path: str) -> None:
                raise ClashError("clash controller is not configured")

            async def measure_delay(self, node_name: str) -> ClashDelayResult:
                raise ClashError("clash controller is not configured")

            async def measure_gateway_delay(self, proxy_url: str) -> ClashDelayResult:
                raise ClashError("clash controller is not configured")

        return cls(settings, profiles, _InactiveController(), catalog=catalog)  # pyright: ignore[reportArgumentType]  # structural controller double

    async def list_nodes(self) -> tuple[ClashProxyNode, ...]:
        return tuple(
            node
            for node in await self._controller.list_nodes()
            if not node.name.startswith(("account-pool-isp-", "account-pool-front-"))
            and node.name.upper() not in _NON_EGRESS_PROXY_NAMES
            and node.proxy_type.casefold() not in _NON_EGRESS_PROXY_TYPES
        )

    async def enforce_inactive_isps(self, today: date) -> None:
        await self.reconcile_isp_bindings(today)

    async def reconcile_isp_bindings(self, today: date) -> None:
        if self._catalog is None:
            return
        async with self._registry_lock:
            await self._reconcile_isp_bindings_locked(today)

    async def _reconcile_isp_bindings_locked(self, today: date) -> None:
        if self._catalog is None:
            return
        bindings: Final = dict(await self._catalog.list_isp_bindings())
        fronts: Final = dict(await self._catalog.list_front_bindings())
        available_nodes: Final = frozenset(node.name for node in await self.list_nodes())
        for port in await self._registered_ports():
            profile_id: Final = bindings.get(port)
            profile: Final = None if profile_id is None else await self._catalog.get_isp(profile_id)
            await self._reconcile_gateway_chain(
                port,
                profile,
                profile_bound=profile_id is not None,
                front_node_name=fronts.get(port),
                available_nodes=available_nodes,
                today=today,
            )

    def configuration(self) -> GatewayConfigurationView:
        return GatewayConfigurationView(config_path=self._settings.clash_config_path or None)

    async def update_configuration(
        self, *, content: str | None = None, url: str | None = None, name: str | None = None
    ) -> GatewayConfigurationView:
        if (content is None) == (url is None):
            raise ClashError("provide exactly one of content or url")
        source: Final = content if content is not None else await self._load_subscription(url)
        self._validate_configuration_content(source)
        if self._catalog is not None:
            profile_name: Final = name or datetime.now(timezone.utc).strftime("配置 %Y-%m-%d %H:%M:%S UTC")
            profile: Final = await self.save_configuration_profile(
                profile_name, content=source
            )
            await self.activate_configuration_profile(profile.id)
            return self.configuration()
        async with self._registry_lock:
            await self._apply_configuration_source(source)
        return self.configuration()

    async def initialize_saved_configuration(self) -> None:
        if self._catalog is None or not self._settings.clash_config_path:
            return
        try:
            with open(self._settings.clash_config_path, encoding="utf-8") as handle:
                current: Final = handle.read()
        except OSError:
            return
        await self._catalog.ensure_active_configuration("当前配置", current)

    async def list_configuration_profiles(self) -> tuple[ProxyConfigurationProfileView, ...]:
        if self._catalog is None:
            return ()
        profiles: Final = await self._catalog.list_configuration_views()
        return tuple(
            ProxyConfigurationProfileView(id=profile_id, name=name, created_at=created_at, active=active)
            for profile_id, name, created_at, active in profiles
        )

    async def save_configuration_profile(
        self, name: str, *, content: str | None = None, url: str | None = None
    ) -> ProxyConfigurationProfileView:
        if self._catalog is None:
            raise ClashError("proxy configuration catalog is not configured")
        normalized_name: Final = name.strip()
        if not normalized_name or len(normalized_name) > 120:
            raise ClashError("configuration name is invalid")
        if (content is None) == (url is None):
            raise ClashError("provide exactly one of content or url")
        source: Final = content if content is not None else await self._load_subscription(url)
        self._validate_configuration_content(source)
        try:
            parsed: Final = yaml.safe_load(source)
        except yaml.YAMLError as error:
            raise ClashError("proxy configuration is invalid YAML") from error
        if not isinstance(parsed, dict):
            raise ClashError("clash config must be a YAML object")
        profile: Final = await self._catalog.save_configuration(normalized_name, source, url)
        return ProxyConfigurationProfileView(
            id=profile.id,
            name=profile.name,
            created_at=profile.created_at,
            active=False,
        )

    async def activate_configuration_profile(self, profile_id: UUID) -> ProxyConfigurationProfileView:
        if self._catalog is None:
            raise ClashError("proxy configuration catalog is not configured")
        profile: Final = await self._catalog.get_configuration(profile_id)
        if profile is None:
            raise ClashError("proxy configuration profile was not found")
        config_path: Final = self._settings.clash_config_path
        if not config_path:
            raise ClashError("clash config path is not configured")
        try:
            async with self._registry_lock:
                with open(config_path, encoding="utf-8") as handle:
                    original: Final = handle.read()
                try:
                    await self._apply_configuration_source(profile.content)
                    await self._reconcile_isp_bindings_locked(date.today())
                    if not await self._catalog.activate_configuration(profile_id):
                        raise ClashError("proxy configuration activation failed")
                except Exception:
                    with open(config_path, encoding="utf-8") as handle:
                        applied: Final = handle.read()
                    await self._write_and_reload(config_path, original, applied)
                    raise
        except ClashError:
            raise
        except OSError as error:
            raise ClashError("clash configuration update failed") from error
        return ProxyConfigurationProfileView(
            id=profile.id,
            name=profile.name,
            created_at=profile.created_at,
            active=True,
        )

    async def delete_configuration_profile(self, profile_id: UUID) -> None:
        if self._catalog is None:
            raise ClashError("proxy configuration catalog is not configured")
        if not await self._catalog.delete_configuration(profile_id):
            raise ClashError("active or missing proxy configuration cannot be deleted")

    async def _apply_configuration_source(self, source: str) -> None:
        config_path: Final = self._settings.clash_config_path
        if not config_path:
            raise ClashError("clash config path is not configured")
        try:
            with open(config_path, encoding="utf-8") as handle:
                original: Final = handle.read()
            original_config: Final = cast(object, yaml.safe_load(original))
            parsed_value: Final = cast(object, yaml.safe_load(source))
        except (OSError, yaml.YAMLError) as error:
            raise ClashError("clash configuration update failed") from error
        if not isinstance(parsed_value, dict):
            raise ClashError("clash config must be a YAML object")
        parsed: Final = cast(dict[object, object], parsed_value)
        await self._preserve_registered_gateways(parsed, original_config)
        updated: Final = yaml.safe_dump(parsed, allow_unicode=True, sort_keys=False)
        await self._write_and_reload(config_path, updated, original)

    async def _load_subscription(self, url: str | None) -> str:
        if url is None:
            raise ClashError("subscription URL is required")
        self._validate_subscription_url(url)
        try:
            return await self._fetch_configuration(url)
        except ClashError:
            raise
        except Exception as error:
            raise ClashError("proxy configuration download failed") from error

    async def fetch_isp_subscription(self, url: str) -> str:
        return await self._load_subscription(url)

    async def _preserve_registered_gateways(self, config: dict[object, object], original: object) -> None:
        ports: Final = await self._registered_ports()
        if not ports or not isinstance(original, dict):
            return
        original_map: Final = cast(dict[object, object], original)
        listener_names: Final = frozenset(f"gateway-{port}" for port in ports)
        bindings: Final = () if self._catalog is None else await self._catalog.list_isp_bindings()
        bound_ports: Final = frozenset(port for port, _ in bindings)
        group_names: Final = frozenset(self.gateway_profile_id(port) for port in ports)
        front_names: Final = frozenset(self.front_selector_name(port) for port in ports)

        def entry_name(item: object) -> object:
            return cast(dict[object, object], item).get("name") if isinstance(item, dict) else None

        def named_entries(value: object, names: frozenset[str]) -> tuple[dict[object, object], ...]:
            if not isinstance(value, list):
                return ()
            entries: Final = cast(list[object], value)
            return tuple(
                cast(dict[object, object], item)
                for item in entries
                if isinstance(item, dict) and entry_name(cast(object, item)) in names
            )

        def without_names(value: object, names: frozenset[str]) -> tuple[object, ...]:
            if not isinstance(value, list):
                return ()
            entries: Final = cast(list[object], value)
            return tuple(item for item in entries if entry_name(item) not in names)

        preserved_listeners: Final = named_entries(original_map.get("listeners"), listener_names)
        existing_groups: Final = named_entries(original_map.get("proxy-groups"), group_names)
        configured_groups: Final = named_entries(config.get("proxy-groups"), group_names)
        group_templates: Final = {
            cast(str, entry_name(group)): group for group in (*existing_groups, *configured_groups)
        }
        managed_group_names: Final = group_names | front_names
        available: Final = tuple(
            name
            for key in ("proxies", "proxy-groups")
            for item in cast(list[object], config.get(key, []))
            if isinstance(config.get(key, []), list)
            and isinstance(item, dict)
            and isinstance((name := entry_name(item)), str)
            and name not in managed_group_names
            and not name.startswith(("account-pool-isp-", "account-pool-front-"))
        )
        preserved_groups: Final = tuple(
            {
                **(
                    {"name": self._selector_name(port), "type": "select"}
                    if port in bound_ports
                    else group_templates.get(
                        self._selector_name(port), {"name": self._selector_name(port), "type": "select"}
                    )
                ),
                "name": self._selector_name(port),
                "type": "select",
                "proxies": ["REJECT"] if port in bound_ports else list(available) or ["REJECT"],
            }
            for port in ports
        )
        preserved_front_groups: Final = tuple(
            {
                **next(
                    (
                        group
                        for group in named_entries(original_map.get("proxy-groups"), front_names)
                        if entry_name(group) == self.front_selector_name(port)
                    ),
                    {"name": self.front_selector_name(port), "type": "select"},
                ),
                "name": self.front_selector_name(port),
                "type": "select",
                "proxies": ["REJECT"],
            }
            for port in ports
        )
        config["listeners"] = (*without_names(config.get("listeners"), listener_names), *preserved_listeners)
        config["proxy-groups"] = (
            *without_names(config.get("proxy-groups"), managed_group_names),
            *preserved_groups,
            *preserved_front_groups,
        )
        config["proxies"] = tuple(
            item
            for item in cast(list[object], config.get("proxies", []))
            if isinstance(config.get("proxies", []), list)
            and (name := entry_name(item)) is None
            or isinstance(name, str) and not name.startswith("account-pool-isp-")
        )

    @staticmethod
    def _validate_configuration_content(content: str) -> None:
        if not content.strip():
            raise ClashError("proxy configuration cannot be empty")
        if len(content.encode("utf-8")) > _MAX_CONFIGURATION_BYTES:
            raise ClashError("proxy configuration is too large")

    @staticmethod
    def _validate_subscription_url(url: str) -> None:
        if len(url) > _MAX_CONFIGURATION_URL_LENGTH:
            raise ClashError("subscription URL is too long")
        try:
            parsed: Final = urlsplit(url)
            port: Final = parsed.port
        except ValueError as error:
            raise ClashError("subscription URL is invalid") from error
        hostname: Final = parsed.hostname
        if (
            parsed.scheme not in {"http", "https"}
            or hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            and not 1 <= port <= 65535
        ):
            raise ClashError("subscription URL must be an HTTP(S) URL without credentials")
        normalized_host: Final = hostname.lower().rstrip(".")
        if normalized_host in {"localhost", "host.docker.internal"} or normalized_host.endswith((".local", ".internal")):
            raise ClashError("subscription URL must not target a local host")
        try:
            address: Final = ipaddress.ip_address(normalized_host)
        except ValueError:
            return
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_unspecified:
            raise ClashError("subscription URL must not target a private address")

    @staticmethod
    async def _download_configuration(url: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=20.0, trust_env=False, follow_redirects=False) as client:
                response: Final = await client.get(url)
        except httpx.HTTPError as error:
            raise ClashError("proxy configuration download failed") from error
        if 300 <= response.status_code < 400:
            raise ClashError("subscription URL redirects are not allowed")
        if response.status_code != 200:
            raise ClashError("proxy configuration download failed")
        if len(response.content) > _MAX_CONFIGURATION_BYTES:
            raise ClashError("proxy configuration is too large")
        try:
            return response.content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ClashError("proxy configuration must be UTF-8 YAML") from error

    @staticmethod
    def gateway_profile_id(port: int) -> str:
        return f"{_GATEWAY_PROFILE_PREFIX}{port}"

    @staticmethod
    def gateway_profile_name(port: int) -> str:
        return f"Clash 端口 {port}"

    async def list_gateways(self) -> tuple[GatewayView, ...]:
        ports: Final = await self._registered_ports()
        if not ports:
            return ()
        bindings: Final = () if self._catalog is None else await self._catalog.list_isp_bindings()
        bound_profiles: Final = dict(bindings)
        fronts: Final = () if self._catalog is None else await self._catalog.list_front_bindings()
        front_bindings: Final = dict(fronts)
        views: list[GatewayView] = []
        for port in ports:
            selector: Final = self._selector_name(port)
            try:
                current_node: Final = await self._controller.selector_current(selector)
            except ClashError:
                current_node = None
            views.append(
                GatewayView(
                    port=port,
                    profile_id=self.gateway_profile_id(port),
                    name=self.gateway_profile_name(port),
                    proxy_url=self._gateway_proxy_url(port),
                    current_node=current_node,
                    isp_profile_id=bound_profiles.get(port),
                    front_node_name=front_bindings.get(port),
                )
            )
        return tuple(views)

    async def switch_gateway(self, port: int, node_name: str) -> GatewayView:
        if node_name.startswith("account-pool-isp-"):
            raise ClashError("ISP nodes must use the ISP binding endpoint")
        async with self._registry_lock:
            return await self._switch_gateway(port, node_name)

    async def _switch_gateway(self, port: int, node_name: str) -> GatewayView:
        if port not in await self._registered_ports():
            raise ClashError(f"gateway port {port} is not registered")
        if self._catalog is not None and any(bound_port == port for bound_port, _ in await self._catalog.list_isp_bindings()):
            await self._set_gateway_node(port, node_name)
        await self._controller.switch_selector(self._selector_name(port), node_name)
        if self._catalog is not None:
            await self._catalog.unbind_isp(port)
            await self._catalog.bind_front_node(port, None)
        current_node: Final = await self._controller.selector_current(self._selector_name(port))
        return GatewayView(
            port=port,
            profile_id=self.gateway_profile_id(port),
            name=self.gateway_profile_name(port),
            proxy_url=self._gateway_proxy_url(port),
            current_node=current_node,
        )

    async def bind_isp_gateway(self, port: int, profile_id: UUID, today: date | None = None) -> GatewayView:
        async with self._registry_lock:
            return await self._bind_isp_gateway(port, profile_id, today or date.today())

    async def _bind_isp_gateway(self, port: int, profile_id: UUID, today: date) -> GatewayView:
        if self._catalog is None:
            raise ClashError("ISP proxy catalog is not configured")
        if port not in await self._registered_ports():
            raise ClashError(f"gateway port {port} is not registered")
        profile: Final = await self._catalog.get_isp(profile_id)
        if profile is None or not self._isp_profile_active(profile, today):
            raise ClashError("ISP profile is unavailable or expired")
        previous: Final = dict(await self._catalog.list_isp_bindings()).get(port)
        try:
            await self._catalog.bind_isp(port, profile_id)
            await self._reconcile_isp_bindings_locked(today)
        except Exception as error:
            if previous is None:
                await self._catalog.unbind_isp(port)
            else:
                await self._catalog.bind_isp(port, previous)
            try:
                await self._reconcile_isp_bindings_locked(today)
            except Exception:
                pass
            if isinstance(error, ClashError):
                raise
            raise ClashError("ISP gateway binding failed") from error
        return await self._gateway_view(port)

    async def bind_front_node(self, port: int, node_name: str | None) -> GatewayView:
        async with self._registry_lock:
            if self._catalog is None:
                raise ClashError("ISP proxy catalog is not configured")
            if port not in await self._registered_ports():
                raise ClashError(f"gateway port {port} is not registered")
            available_nodes: Final = frozenset(node.name for node in await self.list_nodes())
            if node_name is not None and node_name not in available_nodes:
                raise ClashError("proxy node is unavailable")
            previous: Final = dict(await self._catalog.list_front_bindings()).get(port)
            try:
                await self._catalog.bind_front_node(port, node_name)
                await self._reconcile_isp_bindings_locked(date.today())
            except Exception as error:
                await self._catalog.bind_front_node(port, previous)
                try:
                    await self._reconcile_isp_bindings_locked(date.today())
                except Exception:
                    pass
                if isinstance(error, ClashError):
                    raise
                raise ClashError("gateway front binding failed") from error
            return await self._gateway_view(port)

    async def probe_gateway(self, port: int) -> GatewayISPProbeResult:
        if self._catalog is None:
            raise ClashError("ISP proxy catalog is not configured")
        if port not in await self._registered_ports():
            raise ClashError(f"gateway port {port} is not registered")
        profile_id: Final = dict(await self._catalog.list_isp_bindings()).get(port)
        front_node_name: Final = dict(await self._catalog.list_front_bindings()).get(port)
        profile: Final = None if profile_id is None else await self._catalog.get_isp(profile_id)
        available_nodes: Final = frozenset(node.name for node in await self.list_nodes())
        if (
            profile_id is None
            or profile is None
            or not self._isp_profile_active(profile, date.today())
            or (front_node_name is not None and front_node_name not in available_nodes)
        ):
            raise ClashError("gateway ISP chain is not active")
        chain_name: Final = self.isp_node_name(profile_id, port)
        try:
            selected_isp: Final = await self._controller.selector_current(self._selector_name(port))
            selected_front: Final = (
                await self._controller.selector_current(self.front_selector_name(port))
                if front_node_name is not None else None
            )
        except ClashError as error:
            raise ClashError("gateway ISP chain is not active") from error
        if selected_isp != chain_name or selected_front != front_node_name:
            raise ClashError("gateway ISP chain is not active")
        return await self._gateway_probe.probe(port, self._gateway_proxy_url(port))

    async def _gateway_view(self, port: int) -> GatewayView:
        return next(view for view in await self.list_gateways() if view.port == port)

    @staticmethod
    def _isp_profile_active(profile: ISPProxyCatalogProfile, today: date) -> bool:
        return profile.enabled and (not isinstance(profile, ISPProxyProfile) or not profile.expired(today))

    @staticmethod
    def isp_node_name(profile_id: UUID, port: int) -> str:
        return f"account-pool-isp-{port}-{profile_id.hex}"

    @staticmethod
    def front_selector_name(port: int) -> str:
        return f"account-pool-front-{port}"

    @staticmethod
    def _isp_proxy_config(profile: ISPProxyCatalogProfile, node_name: str, front_name: str) -> dict[str, object]:
        if isinstance(profile, ISPSubscriptionNodeProfile):
            return {**profile.config, "name": node_name, "dialer-proxy": front_name}
        return {
            "name": node_name,
            "type": "http" if profile.protocol == "https" else profile.protocol,
            "server": profile.host,
            "port": profile.port,
            **({"tls": True} if profile.protocol == "https" else {}),
            **({"username": profile.username} if profile.username else {}),
            **({"password": profile.password} if profile.password else {}),
            "dialer-proxy": front_name,
        }

    async def _reconcile_gateway_chain(
        self,
        port: int,
        profile: ISPProxyCatalogProfile | None,
        *,
        profile_bound: bool,
        front_node_name: str | None,
        available_nodes: frozenset[str],
        today: date,
    ) -> None:
        config_path: Final = self._settings.clash_config_path
        if not config_path:
            raise ClashError("clash config path is not configured")
        try:
            with open(config_path, encoding="utf-8") as handle:
                original: Final = handle.read()
            config_value: Final = yaml.safe_load(original)
        except (OSError, yaml.YAMLError) as error:
            raise ClashError("clash configuration is unavailable") from error
        if not isinstance(config_value, dict):
            raise ClashError("clash config must be a YAML object")
        config: Final = cast(dict[object, object], config_value)
        proxy_value: Final = config.get("proxies", [])
        group_value: Final = config.get("proxy-groups", [])
        if not isinstance(proxy_value, list) or not isinstance(group_value, list):
            raise ClashError("clash config does not contain valid proxies and proxy-groups")
        proxies: Final = cast(list[object], proxy_value)
        groups: Final = cast(list[object], group_value)
        selector: Final = self._selector_name(port)
        front_selector: Final = self.front_selector_name(port)
        chain_prefix: Final = f"account-pool-isp-{port}-"
        chain_active: Final = (
            profile_bound
            and profile is not None
            and self._isp_profile_active(profile, today)
            and (front_node_name is None or front_node_name in available_nodes)
        )
        chain_name: Final = None if profile is None else self.isp_node_name(profile.id, port)
        updated_proxies: Final = tuple(
            item
            for item in proxies
            if not (
                isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and cast(str, item["name"]).startswith(chain_prefix)
            )
        )
        chain_proxy: Final = (
            ()
            if not chain_active or profile is None or chain_name is None
            # 显式 DIRECT 只直连 ISP 服务器，覆盖订阅自带的 dialer-proxy，目标请求仍经过 ISP。
            else (self._isp_proxy_config(profile, chain_name, "DIRECT" if front_node_name is None else front_selector),)
        )
        available_choices: Final = [*sorted(available_nodes), "REJECT"]
        gateway_choices: Final = (
            [chain_name, "REJECT"]
            if chain_active and chain_name is not None
            else ["REJECT"]
            if profile_bound
            else available_choices
        )
        existing_gateway: Final = next(
            (item for item in groups if isinstance(item, dict) and item.get("name") == selector),
            {"name": selector, "type": "select"},
        )
        existing_front: Final = next(
            (item for item in groups if isinstance(item, dict) and item.get("name") == front_selector),
            {"name": front_selector, "type": "select"},
        )
        gateway_base: Final = (
            {"name": selector, "type": "select"} if profile_bound else existing_gateway
        )
        updated_groups: Final = tuple(
            item
            for item in groups
            if not (isinstance(item, dict) and item.get("name") in {selector, front_selector})
        ) + (
            {**gateway_base, "name": selector, "type": "select", "proxies": gateway_choices},
            {**existing_front, "name": front_selector, "type": "select", "proxies": available_choices},
        )
        config["proxies"] = (*updated_proxies, *chain_proxy)
        config["proxy-groups"] = updated_groups
        updated: Final = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
        previous_gateway: Final = await self._selector_current_or_none(selector)
        previous_front: Final = await self._selector_current_or_none(front_selector)
        if profile_bound and not chain_active:
            await self._controller.switch_selector(selector, "REJECT")
        await self._write_and_reload(config_path, updated, original)
        if not profile_bound:
            return
        front_selection: Final = front_node_name if front_node_name in available_nodes else "REJECT"
        gateway_selection: Final = chain_name if chain_active and chain_name is not None else "REJECT"
        try:
            await self._controller.switch_selector(front_selector, front_selection)
            await self._controller.switch_selector(selector, gateway_selection)
        except Exception as error:
            try:
                with open(config_path, encoding="utf-8") as handle:
                    applied: Final = handle.read()
                await self._write_and_reload(config_path, original, applied)
                if previous_front is not None:
                    await self._controller.switch_selector(front_selector, previous_front)
                if previous_gateway is not None:
                    await self._controller.switch_selector(selector, previous_gateway)
            except Exception:
                pass
            raise ClashError("ISP gateway binding failed") from error

    async def _selector_current_or_none(self, selector: str) -> str | None:
        try:
            return await self._controller.selector_current(selector)
        except ClashError:
            return None

    async def _set_gateway_node(self, port: int, node_name: str) -> None:
        if node_name not in {node.name for node in await self.list_nodes()}:
            raise ClashError("proxy node is unavailable")
        config_path: Final = self._settings.clash_config_path
        with open(config_path, encoding="utf-8") as handle:
            original: Final = handle.read()
        config: Final = yaml.safe_load(original)
        selector: Final = self._selector_name(port)
        config["proxy-groups"] = [
            {"name": selector, "type": "select", "proxies": [node_name, "REJECT"]}
            if isinstance(group, dict) and group.get("name") == selector else group
            for group in config.get("proxy-groups", [])
        ]
        await self._write_and_reload(config_path, yaml.safe_dump(config, allow_unicode=True, sort_keys=False), original)

    async def _write_and_reload(self, config_path: str, updated: str, original: str) -> None:
        if updated == original:
            return
        directory: Final = os.path.dirname(config_path) or "."
        try:
            self._replace_config(config_path, directory, updated)
            await self._controller.reload_config(self._settings.clash_runtime_config_path)
        except Exception as error:
            self._replace_config(config_path, directory, original)
            try:
                await self._controller.reload_config(self._settings.clash_runtime_config_path)
            except Exception:
                pass
            raise ClashError("clash configuration update failed") from error

    async def measure_delays(self) -> tuple[GatewayDelayView, ...]:
        ports: Final = await self._registered_ports()
        if not ports:
            return ()
        selectors: Final = tuple(self._selector_name(port) for port in ports)
        selected: Final = await self._controller.selector_currents(selectors)
        # 共用节点只测一次，限制同时检测数量，避免刷新时拥塞代理线路。
        nodes: Final = tuple(dict.fromkeys(node for node in selected if node is not None))
        measurements: Final = await asyncio.gather(*(self._measure_node(node) for node in nodes))
        results: Final = dict(zip(nodes, measurements, strict=True))
        gateway_targets: Final = tuple(
            (port, node) for port, node in zip(ports, selected, strict=True) if node is not None
        )
        gateway_measurements: Final = await asyncio.gather(
            *(self._measure_gateway(port) for port, _ in gateway_targets)
        )
        gateway_results: Final = dict(zip((port for port, _ in gateway_targets), gateway_measurements, strict=True))
        observed: Final = await self._controller.selector_currents(selectors)
        checked_at: Final = datetime.now(timezone.utc)
        # 检测期间节点若被切换，旧结果不能归到新节点名下。
        return tuple(
            GatewayDelayView(
                port,
                current,
                result.status,
                result.delay_ms,
                checked_at,
                gateway_result.status,
                gateway_result.delay_ms,
            )
            for port, previous, current in zip(ports, selected, observed, strict=True)
            for result in (
                results.get(current, ClashDelayResult("error"))
                if current is not None and current == previous
                else ClashDelayResult("error"),
            )
            for gateway_result in (
                gateway_results.get(port, ClashDelayResult("error"))
                if current is not None and current == previous
                else ClashDelayResult("error"),
            )
        )

    async def _measure_node(self, node_name: str) -> ClashDelayResult:
        async with self._delay_slots:
            return await self._controller.measure_delay(node_name)

    async def _measure_gateway(self, port: int) -> ClashDelayResult:
        async with self._delay_slots:
            return await self._controller.measure_gateway_delay(self._gateway_proxy_url(port))

    async def sync_profiles(self) -> int:
        """把每个网关端口 upsert 进代理名单，返回写入条数；账号配置下拉框因此自动出现网关。"""
        ports: Final = await self._registered_ports()
        if not ports:
            return 0
        written: Final = await self._profiles.upsert_gateways(
            tuple(
                (self.gateway_profile_id(port), self.gateway_profile_name(port), self._gateway_proxy_url(port))
                for port in ports
            )
        )
        return written

    async def add_gateway(self) -> GatewayView:
        async with self._registry_lock:
            ports: Final = await self._registered_ports()
            port: Final = next((candidate for candidate in range(7891, 65536) if candidate not in ports), None)
            if port is None:
                raise ClashError("no free proxy gateway port")
            await self._update_config(port, True)
            try:
                await self._register_port(port)
                await self.sync_profiles()
            except Exception as error:
                try:
                    await self._update_config(port, False)
                except Exception:
                    pass
                raise ClashError("proxy gateway registration failed") from error
        views: Final = await self.list_gateways()
        return next(view for view in views if view.port == port)

    async def remove_gateway(self, port: int, referenced_profile_ids: frozenset[str]) -> None:
        async with self._registry_lock:
            ports: Final = await self._registered_ports()
            if port not in ports:
                raise ClashError(f"gateway port {port} is not registered")
            profile_id: Final = self.gateway_profile_id(port)
            if profile_id in referenced_profile_ids:
                raise ClashError("gateway is used by an account card")
            config_path: Final = self._settings.clash_config_path
            if not config_path:
                raise ClashError("clash config path is not configured")
            try:
                with open(config_path, encoding="utf-8") as handle:
                    original_config: Final = handle.read()
            except OSError as error:
                raise ClashError("clash configuration is unavailable") from error
            isp_binding: Final = (
                None
                if self._catalog is None
                else dict(await self._catalog.list_isp_bindings()).get(port)
            )
            front_binding: Final = (
                None
                if self._catalog is None
                else dict(await self._catalog.list_front_bindings()).get(port)
            )
            await self._update_config(port, False)
            try:
                if self._catalog is not None:
                    await self._catalog.unbind_isp(port)
                    await self._catalog.bind_front_node(port, None)
                await self._unregister_port(port)
                await self._profiles.delete_gateway(profile_id)
            except Exception as error:
                if self._catalog is not None:
                    if isp_binding is not None:
                        await self._catalog.bind_isp(port, isp_binding)
                    if front_binding is not None:
                        await self._catalog.bind_front_node(port, front_binding)
                try:
                    await self._register_port(port)
                    with open(config_path, encoding="utf-8") as handle:
                        removed_config: Final = handle.read()
                    await self._write_and_reload(config_path, original_config, removed_config)
                except Exception:
                    pass
                raise ClashError("proxy gateway deletion failed") from error

    async def _registered_ports(self) -> tuple[int, ...]:
        if not self._dynamic_registry:
            return self._settings.clash_gateway_ports
        from account_pool.repository import database_connection

        async with database_connection(self._settings.database_url) as connection:
            cursor: Final = await connection.execute("SELECT port FROM account_pool_proxy_gateways ORDER BY port")
            rows: Final = await cursor.fetchall()
            marker: Final = await connection.execute("SELECT singleton FROM account_pool_proxy_gateway_registry")
            initialized: Final = await marker.fetchone()
            if initialized is None:
                async with connection.cursor() as cursor:
                    await cursor.executemany(
                        "INSERT INTO account_pool_proxy_gateways (port) VALUES (%s) ON CONFLICT DO NOTHING",
                        tuple((port,) for port in self._settings.clash_gateway_ports),
                    )
                await connection.execute("INSERT INTO account_pool_proxy_gateway_registry (singleton) VALUES (true)")
                return tuple(sorted(self._settings.clash_gateway_ports))
        return tuple(int(row["port"]) for row in rows)

    async def _register_port(self, port: int) -> None:
        from account_pool.repository import database_connection

        async with database_connection(self._settings.database_url) as connection:
            await connection.execute("INSERT INTO account_pool_proxy_gateways (port) VALUES (%s)", (port,))

    async def _unregister_port(self, port: int) -> None:
        from account_pool.repository import database_connection

        async with database_connection(self._settings.database_url) as connection:
            await connection.execute("DELETE FROM account_pool_proxy_gateways WHERE port = %s", (port,))

    async def _update_config(self, port: int, add: bool) -> None:
        config_path: Final = self._settings.clash_config_path
        if not config_path:
            raise ClashError("clash config path is not configured")
        with open(config_path, encoding="utf-8") as handle:
            original: Final = handle.read()
        config: Final = yaml.safe_load(original)
        if not isinstance(config, dict):
            raise ClashError("clash config must be a YAML object")
        listeners: Final = config.get("listeners", [])
        groups: Final = config.get("proxy-groups", [])
        if not isinstance(listeners, list) or not isinstance(groups, list):
            raise ClashError("clash config does not contain listeners and proxy-groups")
        name: Final = self.gateway_profile_id(port)
        front_name: Final = self.front_selector_name(port)
        if add:
            if any(isinstance(item, dict) and item.get("name") == f"gateway-{port}" for item in listeners):
                return
            template: Final = next(
                (item for item in listeners if isinstance(item, dict) and item.get("name", "").startswith("gateway-")),
                None,
            )
            group: Final = next(
                (item for item in groups if isinstance(item, dict) and item.get("name", "").startswith("clash-gateway-")),
                None,
            )
            if not isinstance(template, dict) or not isinstance(group, dict):
                raise ClashError("clash gateway template is missing")
            listeners.append({**template, "name": f"gateway-{port}", "port": port, "proxy": name})
            choices: Final = group.get("proxies", [])
            if not isinstance(choices, list) or any(not isinstance(choice, str) for choice in choices):
                raise ClashError("clash gateway template has invalid choices")
            groups.append({**group, "name": name, "proxies": list(dict.fromkeys((*choices, "REJECT")))})
            if not any(isinstance(item, dict) and item.get("name") == front_name for item in groups):
                front_choices: Final = [node.name for node in await self.list_nodes()]
                groups.append(
                    {"name": front_name, "type": "select", "proxies": [*front_choices, "REJECT"]}
                )
        else:
            config["listeners"] = [item for item in listeners if not (isinstance(item, dict) and item.get("name") == f"gateway-{port}")]
            config["proxy-groups"] = [
                item
                for item in groups
                if not (isinstance(item, dict) and item.get("name") in {name, front_name})
            ]
            proxy_value: Final = config.get("proxies", [])
            if not isinstance(proxy_value, list):
                raise ClashError("clash config does not contain valid proxies")
            config["proxies"] = [
                item
                for item in proxy_value
                if not (
                    isinstance(item, dict)
                    and isinstance(item.get("name"), str)
                    and cast(str, item["name"]).startswith(f"account-pool-isp-{port}-")
                )
            ]
        updated: Final = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
        await self._write_and_reload(config_path, updated, original)

    @staticmethod
    def _replace_config(config_path: str, directory: str, content: str) -> None:
        fd, temporary = tempfile.mkstemp(prefix="mihomo-config-", dir=directory, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, config_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _gateway_proxy_url(self, port: int) -> str:
        """容器内的可达地址：Clash 装在宿主机时 127.0.0.1 不可达，统一走 proxy_gateway_host。"""
        return f"http://{self._settings.proxy_gateway_host}:{port}"

    def _selector_name(self, port: int) -> str:
        return self.gateway_profile_id(port)
