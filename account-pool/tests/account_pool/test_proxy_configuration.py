"""测试代理配置上传、订阅下载、校验和失败回滚。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Final, cast
from uuid import UUID, uuid4

import pytest
from account_pool.api import create_router
from account_pool.clash import ClashController, ClashError, ClashProxyNode
from account_pool.config import Settings
from account_pool.ports import ProxyProfileRepository
from account_pool.proxy_catalog import ISPProxyProfile, ProxyConfigurationProfile
from account_pool.proxy_catalog_repository import ProxyCatalogRepository
from account_pool.proxy_gateways import GatewayConfigurationView, ProxyGatewayService
from account_pool.service import EnvironmentService
from fastapi import FastAPI
from fastapi.testclient import TestClient


@dataclass
class ConfigController:
    reload_error: bool = False
    reloaded_paths: list[str] = field(default_factory=list)

    async def list_nodes(self) -> tuple[ClashProxyNode, ...]:
        return ()

    async def selector_current(self, selector_name: str) -> str | None:
        return None

    async def selector_currents(self, selector_names: tuple[str, ...]) -> tuple[str | None, ...]:
        return tuple(None for _ in selector_names)

    async def switch_selector(self, selector_name: str, node_name: str) -> None:
        return None

    async def reload_config(self, path: str) -> None:
        self.reloaded_paths.append(path)
        if self.reload_error:
            raise ClashError("reload failed")


@dataclass
class Profiles:
    async def list(self):
        return ()

    async def upsert_gateways(self, gateways: tuple[tuple[str, str, str], ...]) -> int:
        return len(tuple(gateways))


@dataclass(frozen=True)
class ConfigurationService:
    gateway: ProxyGatewayService

    async def update_proxy_gateway_configuration(
        self, *, content: str | None = None, url: str | None = None, name: str | None = None
    ) -> GatewayConfigurationView:
        return await self.gateway.update_configuration(content=content, url=url, name=name)


def _settings(config_path: str, ports: tuple[int, ...] = ()) -> Settings:
    return Settings(
        database_url="postgresql://test:test@db:5432/test",
        manager_token="t" * 32,
        secret_seed="s" * 32,
        ssh_host="example.test",
        ssh_user="deploy",
        clash_config_path=config_path,
        clash_runtime_config_path="/runtime/config.yaml",
        clash_gateway_ports=ports,
    )


def _service(
    config_path: str,
    controller: ConfigController,
    fetcher: Callable[[str], Awaitable[str]] | None = None,
) -> ProxyGatewayService:
    return ProxyGatewayService(
        _settings(config_path),
        cast(ProxyProfileRepository, Profiles()),
        cast(ClashController, controller),
        fetch_configuration=fetcher,
    )


@pytest.mark.asyncio
async def test_update_configuration_replaces_yaml_and_reloads(tmp_path: Path) -> None:
    config_path: Final = tmp_path / "config.yaml"
    config_path.write_text("listeners:\n  - name: old\n", encoding="utf-8")
    controller: Final = ConfigController()
    service: Final = _service(str(config_path), controller)

    await service.update_configuration(content="listeners:\n  - name: new\n")

    assert config_path.read_text(encoding="utf-8") == "listeners:\n- name: new\n"
    assert controller.reloaded_paths == ["/runtime/config.yaml"]


@pytest.mark.asyncio
async def test_update_configuration_rolls_back_when_reload_fails(tmp_path: Path) -> None:
    config_path: Final = tmp_path / "config.yaml"
    original: Final = "listeners:\n  - name: old\n"
    config_path.write_text(original, encoding="utf-8")
    controller: Final = ConfigController(reload_error=True)
    service: Final = _service(str(config_path), controller)

    with pytest.raises(ClashError, match="configuration update failed"):
        await service.update_configuration(content="listeners:\n  - name: new\n")

    assert config_path.read_text(encoding="utf-8") == original
    assert controller.reloaded_paths == ["/runtime/config.yaml", "/runtime/config.yaml"]


@pytest.mark.asyncio
async def test_update_configuration_fetches_subscription_without_redirect(tmp_path: Path) -> None:
    config_path: Final = tmp_path / "config.yaml"
    config_path.write_text("listeners: []\n", encoding="utf-8")
    requested: list[str] = []

    async def fetcher(url: str) -> str:
        requested.append(url)
        return "listeners:\n  - name: downloaded\n"

    service: Final = _service(config_path.as_posix(), ConfigController(), fetcher)

    await service.update_configuration(url="https://example.test/subscription.yaml")

    assert requested == ["https://example.test/subscription.yaml"]
    assert "downloaded" in config_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_update_configuration_preserves_existing_gateway_entries(tmp_path: Path) -> None:
    config_path: Final = tmp_path / "config.yaml"
    config_path.write_text(
        "listeners:\n  - name: gateway-7891\n    port: 7891\n    proxy: clash-gateway-7891\n"
        "proxy-groups:\n  - name: clash-gateway-7891\n    type: select\n    proxies: [old]\n",
        encoding="utf-8",
    )
    service: Final = ProxyGatewayService(
        _settings(str(config_path), (7891,)),
        cast(ProxyProfileRepository, Profiles()),
        cast(ClashController, ConfigController()),
    )

    await service.update_configuration(content="listeners: []\nproxy-groups: []\n")

    updated: Final = config_path.read_text(encoding="utf-8")
    assert "gateway-7891" in updated
    assert "clash-gateway-7891" in updated


@pytest.mark.asyncio
async def test_configuration_profiles_are_saved_separately_and_switchable(tmp_path: Path) -> None:
    class Catalog:
        def __init__(self) -> None:
            self.profiles: dict[UUID, ProxyConfigurationProfile] = {}
            self.active_id: UUID | None = None

        async def list_isp_bindings(self) -> tuple[tuple[int, UUID], ...]:
            return ()

        async def list_front_bindings(self) -> tuple[tuple[int, str], ...]:
            return ()

        async def list_configuration_views(self) -> tuple[tuple[UUID, str, datetime, bool], ...]:
            return tuple(
                (profile.id, profile.name, profile.created_at, profile.id == self.active_id)
                for profile in self.profiles.values()
            )

        async def save_configuration(self, name: str, content: str, source_url: str | None):
            profile: Final = ProxyConfigurationProfile(
                id=uuid4(), name=name, content=content, source_url=source_url, created_at=datetime.now(timezone.utc)
            )
            self.profiles[profile.id] = profile
            return profile

        async def get_configuration(self, profile_id: UUID):
            return self.profiles.get(profile_id)

        async def activate_configuration(self, profile_id: UUID) -> bool:
            self.active_id = profile_id
            return profile_id in self.profiles

    config_path: Final = tmp_path / "config.yaml"
    initial: Final = "external-controller: 127.0.0.1:9090\nlisteners: []\nproxy-groups: []\n"
    first: Final = "external-controller: 127.0.0.1:9091\nlisteners: []\nproxy-groups: []\n"
    second: Final = "external-controller: 127.0.0.1:9092\nlisteners: []\nproxy-groups: []\n"
    config_path.write_text(initial, encoding="utf-8")
    controller: Final = ConfigController()
    catalog: Final = Catalog()
    service: Final = ProxyGatewayService(
        _settings(str(config_path)),
        cast(ProxyProfileRepository, Profiles()),
        cast(ClashController, controller),
        catalog=cast(ProxyCatalogRepository, catalog),
    )

    saved_first: Final = await service.save_configuration_profile("First", content=first)
    saved_second: Final = await service.save_configuration_profile("Second", content=second)

    assert config_path.read_text(encoding="utf-8") == initial
    assert len(catalog.profiles) == 2
    await service.activate_configuration_profile(saved_second.id)
    assert "9092" in config_path.read_text(encoding="utf-8")
    await service.activate_configuration_profile(saved_first.id)
    assert "9091" in config_path.read_text(encoding="utf-8")
    assert len(catalog.profiles) == 2


def test_configuration_api_rejects_ambiguous_and_invalid_inputs(tmp_path: Path) -> None:
    config_path: Final = tmp_path / "config.yaml"
    config_path.write_text("listeners: []\n", encoding="utf-8")
    service: Final = cast(EnvironmentService, ConfigurationService(_service(str(config_path), ConfigController())))
    app: Final = FastAPI()
    app.include_router(create_router(service, "t" * 32))

    with TestClient(app) as client:
        both: Final = client.put(
            "/api/proxy-gateways/configuration",
            headers={"Authorization": "Bearer " + "t" * 32},
            json={"content": "listeners: []", "url": "https://example.test/config.yaml"},
        )
        scalar: Final = client.put(
            "/api/proxy-gateways/configuration",
            headers={"Authorization": "Bearer " + "t" * 32},
            json={"content": "[]"},
        )
        private: Final = client.put(
            "/api/proxy-gateways/configuration",
            headers={"Authorization": "Bearer " + "t" * 32},
            json={"url": "http://127.0.0.1/config.yaml"},
        )

    assert both.status_code == 422
    assert scalar.status_code == 422
    assert private.status_code == 422


@pytest.mark.asyncio
async def test_isp_expiry_persists_reject_and_edit_refreshes_bound_node(tmp_path: Path) -> None:
    import yaml

    profile_id: Final = uuid4()

    class Catalog:
        profile = ISPProxyProfile(id=profile_id, name="ISP", protocol="http", host="isp.test", port=1234,
                                  password="first-secret", expires_on=date(2026, 11, 1))
        bindings: tuple[tuple[int, UUID], ...] = ()
        fronts: tuple[tuple[int, str], ...] = ((7891, "airport-a"),)

        async def get_isp(self, identity):
            return self.profile if identity == profile_id else None

        async def list_isp_bindings(self):
            return self.bindings

        async def list_front_bindings(self):
            return self.fronts

        async def list_inactive_isp_bindings(self, today):
            return self.bindings if not self.profile.enabled or self.profile.expired(today) else ()

        async def bind_isp(self, port, identity):
            self.bindings = tuple(binding for binding in self.bindings if binding[0] != port) + ((port, identity),)

        async def unbind_isp(self, port):
            self.bindings = tuple(binding for binding in self.bindings if binding[0] != port)

        async def bind_front_node(self, port, node_name):
            self.fronts = tuple(binding for binding in self.fronts if binding[0] != port)
            if node_name is not None:
                self.fronts = (*self.fronts, (port, node_name))

    class Controller(ConfigController):
        current: dict[str, str] = {}

        async def list_nodes(self):
            return (ClashProxyNode("airport-a", "Socks5"),)

        async def switch_selector(self, selector_name, node_name):
            self.current[selector_name] = node_name

        async def selector_current(self, selector_name):
            return self.current.get(selector_name)

    config_path: Final = tmp_path / "config.yaml"
    config_path.write_text(
        "proxies:\n"
        "  - {name: old, type: http, server: old.test, port: 80}\n"
        "  - {name: airport-a, type: socks5, server: airport.test, port: 443}\n"
        "listeners: [{name: gateway-7891, port: 7891, proxy: clash-gateway-7891}]\n"
        "proxy-groups: [{name: clash-gateway-7891, type: select, proxies: [old]}]\n",
        encoding="utf-8",
    )
    catalog: Final = Catalog()
    controller: Final = Controller()
    service: Final = ProxyGatewayService(_settings(str(config_path), (7891,)), cast(ProxyProfileRepository, Profiles()),
                                        cast(ClashController, controller), catalog=cast(ProxyCatalogRepository, catalog))
    await service.bind_isp_gateway(7891, profile_id, date(2026, 10, 14))
    catalog.profile = catalog.profile.model_copy(update={"password": "new-secret"})
    await service.reconcile_isp_bindings(date(2026, 10, 14))
    assert "new-secret" in config_path.read_text(encoding="utf-8")
    await service.reconcile_isp_bindings(date(2026, 11, 1))
    config: Final = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    group: Final = next(group for group in config["proxy-groups"] if group["name"] == "clash-gateway-7891")
    assert group["proxies"][0] == "REJECT"
    assert all(proxy["name"] != service.isp_node_name(profile_id, 7891) for proxy in config["proxies"])
    assert controller.current["clash-gateway-7891"] == "REJECT"
    with pytest.raises(ClashError):
        await service.switch_gateway(7891, service.isp_node_name(profile_id, 7891))
