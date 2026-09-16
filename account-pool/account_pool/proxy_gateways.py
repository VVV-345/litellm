"""本模块编排 Clash 代理网关：把各端口登记为代理名单条目、查询状态、切换出口节点。"""

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

import yaml

from account_pool.clash import ClashController, ClashDelayResult, ClashDelayStatus, ClashError, ClashProxyNode
from account_pool.config import Settings
from account_pool.ports import ProxyProfileRepository

_GATEWAY_PROFILE_PREFIX: Final = "clash-gateway-"


@dataclass(frozen=True, slots=True)
class GatewayView:
    port: int
    profile_id: str
    name: str
    proxy_url: str
    current_node: str | None


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


class ProxyGatewayService:
    """对外提供代理网关管理；账号侧继续消费既有 ProxyProfileRepository，互不感知。"""

    def __init__(
        self,
        settings: Settings,
        profiles: ProxyProfileRepository,
        controller: ClashController,
    ) -> None:
        self._settings: Final = settings
        self._profiles: Final = profiles
        self._controller: Final = controller
        self._delay_slots: Final = asyncio.Semaphore(10)
        self._registry_lock: Final = asyncio.Lock()
        self._dynamic_registry: Final = callable(getattr(profiles, "delete_gateway", None))

    @classmethod
    def disabled(cls, settings: Settings, profiles: ProxyProfileRepository) -> ProxyGatewayService:
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

        return cls(settings, profiles, _InactiveController())  # pyright: ignore[reportArgumentType]  # structural controller double

    async def list_nodes(self) -> tuple[ClashProxyNode, ...]:
        return await self._controller.list_nodes()

    def configuration(self) -> GatewayConfigurationView:
        return GatewayConfigurationView(config_path=self._settings.clash_config_path or None)

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
                )
            )
        return tuple(views)

    async def switch_gateway(self, port: int, node_name: str) -> GatewayView:
        if port not in await self._registered_ports():
            raise ClashError(f"gateway port {port} is not registered")
        await self._controller.switch_selector(self._selector_name(port), node_name)
        current_node: Final = await self._controller.selector_current(self._selector_name(port))
        return GatewayView(
            port=port,
            profile_id=self.gateway_profile_id(port),
            name=self.gateway_profile_name(port),
            proxy_url=self._gateway_proxy_url(port),
            current_node=current_node,
        )

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
        observed: Final = await self._controller.selector_currents(selectors)
        checked_at: Final = datetime.now(timezone.utc)
        # 检测期间节点若被切换，旧结果不能归到新节点名下。
        return tuple(
            GatewayDelayView(port, current, result.status, result.delay_ms, checked_at)
            for port, previous, current in zip(ports, selected, observed, strict=True)
            for result in (
                results.get(current, ClashDelayResult("error"))
                if current is not None and current == previous
                else ClashDelayResult("error"),
            )
        )

    async def _measure_node(self, node_name: str) -> ClashDelayResult:
        async with self._delay_slots:
            return await self._controller.measure_delay(node_name)

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
            await self._update_config(port, False)
            try:
                await self._unregister_port(port)
                await self._profiles.delete_gateway(profile_id)
            except Exception as error:
                try:
                    await self._update_config(port, True)
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
                await connection.executemany(
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
            groups.append({**group, "name": name})
        else:
            config["listeners"] = [item for item in listeners if not (isinstance(item, dict) and item.get("name") == f"gateway-{port}")]
            config["proxy-groups"] = [item for item in groups if not (isinstance(item, dict) and item.get("name") == name)]
        directory: Final = os.path.dirname(config_path) or "."
        updated: Final = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
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
