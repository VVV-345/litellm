"""本模块编排 Clash 代理网关：把各端口登记为代理名单条目、查询状态、切换出口节点。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from account_pool.clash import ClashController, ClashError, ClashProxyNode
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

    @classmethod
    def disabled(cls, settings: Settings, profiles: ProxyProfileRepository) -> ProxyGatewayService:
        """Clash 未配置时的占位实例：列表为空，调用切换即报错。"""

        class _InactiveController:
            async def list_nodes(self) -> tuple[ClashProxyNode, ...]:
                return ()

            async def selector_current(self, selector_name: str) -> str | None:
                raise ClashError("clash controller is not configured")

            async def switch_selector(self, selector_name: str, node_name: str) -> None:
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
        ports: Final = self._settings.clash_gateway_ports
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
        if port not in self._settings.clash_gateway_ports:
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

    async def sync_profiles(self) -> int:
        """把每个网关端口 upsert 进代理名单，返回写入条数；账号配置下拉框因此自动出现网关。"""
        ports: Final = self._settings.clash_gateway_ports
        if not ports:
            return 0
        written: Final = await self._profiles.upsert_gateways(
            tuple(
                (self.gateway_profile_id(port), self.gateway_profile_name(port), self._gateway_proxy_url(port))
                for port in ports
            )
        )
        return written

    def _gateway_proxy_url(self, port: int) -> str:
        """容器内的可达地址：Clash 装在宿主机时 127.0.0.1 不可达，统一走 proxy_gateway_host。"""
        return f"http://{self._settings.proxy_gateway_host}:{port}"

    def _selector_name(self, port: int) -> str:
        return self.gateway_profile_id(port)
