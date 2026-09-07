"""测试代理网关编排层：网关列表、切换、名单同步与未配置占位行为。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, cast
from urllib.parse import urlsplit

import pytest
from account_pool.api import create_router
from account_pool.clash import ClashError, ClashProxyNode
from account_pool.config import Settings
from account_pool.proxy_gateways import GatewayConfigurationView, GatewayView, ProxyGatewayService
from account_pool.service import EnvironmentService
from fastapi import FastAPI
from fastapi.testclient import TestClient


@dataclass
class FakeController:
    nodes: tuple[ClashProxyNode, ...] = ()
    current: dict[str, str] = field(default_factory=dict)
    switched: list[tuple[str, str]] = field(default_factory=list)
    fail_current: bool = False

    async def list_nodes(self) -> tuple[ClashProxyNode, ...]:
        return self.nodes

    async def selector_current(self, selector_name: str) -> str | None:
        if self.fail_current:
            raise ClashError("unreachable")
        return self.current.get(selector_name)

    async def switch_selector(self, selector_name: str, node_name: str) -> None:
        self.switched.append((selector_name, node_name))
        self.current[selector_name] = node_name


@dataclass
class FakeProfiles:
    upserts: list[tuple[str, str, str]] = field(default_factory=list)

    async def list(self):
        return ()

    async def get_url(self, profile_id: str):
        return None

    async def upsert_gateways(self, gateways) -> int:
        self.upserts.extend(gateways)
        return len(gateways)


def _settings(ports: tuple[int, ...], controller_url: str = "http://127.0.0.1:9090") -> Settings:
    return Settings(
        database_url="postgresql://test:test@db:5432/test",
        manager_token="t" * 32,
        secret_seed="s" * 32,
        ssh_host="example.test",
        ssh_user="deploy",
        clash_controller_url=controller_url,
        clash_gateway_ports=ports,
    )


def _service(
    settings: Settings,
    controller: FakeController,
    profiles: FakeProfiles | None = None,
) -> tuple[ProxyGatewayService, FakeProfiles]:
    resolved_profiles: Final = profiles or FakeProfiles()
    return ProxyGatewayService(settings, resolved_profiles, controller), resolved_profiles  # pyright: ignore[reportArgumentType]  # test doubles satisfy protocols


@pytest.mark.asyncio
async def test_list_gateways_reports_selector_current_with_container_reachable_host() -> None:
    controller: Final = FakeController(current={"clash-gateway-7891": "美国01"})
    service, _ = _service(_settings((7891, 7892)), controller)

    views: Final = await service.list_gateways()

    assert views == (
        GatewayView(
            port=7891,
            profile_id="clash-gateway-7891",
            name="Clash 端口 7891",
            proxy_url="http://host.docker.internal:7891",
            current_node="美国01",
        ),
        GatewayView(
            port=7892,
            profile_id="clash-gateway-7892",
            name="Clash 端口 7892",
            proxy_url="http://host.docker.internal:7892",
            current_node=None,
        ),
    )


@pytest.mark.asyncio
async def test_switch_gateway_updates_selector_and_view() -> None:
    controller: Final = FakeController()
    service, _ = _service(_settings((7891,)), controller)

    view: Final = await service.switch_gateway(7891, "日本02")

    assert controller.switched == [("clash-gateway-7891", "日本02")]
    assert view.current_node == "日本02"
    assert view.proxy_url == "http://host.docker.internal:7891"


@pytest.mark.asyncio
async def test_switch_gateway_rejects_unregistered_port() -> None:
    service, _ = _service(_settings((7891,)), FakeController())

    with pytest.raises(ClashError):
        await service.switch_gateway(9999, "美国01")


@pytest.mark.asyncio
async def test_sync_profiles_upserts_gateway_entries() -> None:
    profiles: Final = FakeProfiles()
    service, _ = _service(_settings((7891, 7892)), FakeController(), profiles)

    written: Final = await service.sync_profiles()

    assert written == 2
    assert profiles.upserts == [
        ("clash-gateway-7891", "Clash 端口 7891", "http://host.docker.internal:7891"),
        ("clash-gateway-7892", "Clash 端口 7892", "http://host.docker.internal:7892"),
    ]


@pytest.mark.asyncio
async def test_disabled_service_reports_empty_nodes_and_raises_on_switch() -> None:
    service, _ = _service(_settings(()), FakeController())
    disabled: Final = ProxyGatewayService.disabled(_settings(()), FakeProfiles())

    assert await disabled.list_nodes() == ()
    assert await service.list_gateways() == ()
    with pytest.raises(ClashError):
        await disabled.switch_gateway(7891, "美国01")


def test_gateway_profile_id_and_selector_share_naming() -> None:
    assert ProxyGatewayService.gateway_profile_id(7891) == "clash-gateway-7891"
    parsed: Final = urlsplit(_settings((7891,)).clash_controller_url)
    assert parsed.hostname == "127.0.0.1"


def test_gateway_configuration_reports_only_the_declared_config_path() -> None:
    settings: Final = Settings(
        database_url="postgresql://test:test@db:5432/test",
        manager_token="t" * 32,
        secret_seed="s" * 32,
        ssh_host="example.test",
        ssh_user="deploy",
        clash_config_path="/opt/litellm/mihomo/config.yaml",
    )
    service, _ = _service(settings, FakeController())

    assert service.configuration().config_path == "/opt/litellm/mihomo/config.yaml"


@dataclass(frozen=True)
class ConfigurationService:
    config_path: str | None

    def proxy_gateway_configuration(self) -> GatewayConfigurationView:
        return GatewayConfigurationView(config_path=self.config_path)


def test_manager_api_returns_declared_configuration_path() -> None:
    app: Final = FastAPI()
    manager_token: Final = "t" * 32
    service: Final = cast(EnvironmentService, ConfigurationService("/opt/litellm/mihomo/config.yaml"))
    app.include_router(create_router(service, manager_token))

    with TestClient(app) as client:
        response: Final = client.get(
            "/api/proxy-gateways/configuration",
            headers={"Authorization": f"Bearer {manager_token}"},
        )

    assert response.status_code == 200
    assert response.json() == {"config_path": "/opt/litellm/mihomo/config.yaml"}
