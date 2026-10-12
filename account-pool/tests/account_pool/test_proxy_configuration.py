"""测试代理配置上传、订阅下载、校验和失败回滚。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast

import pytest
from account_pool.api import create_router
from account_pool.clash import ClashController, ClashError, ClashProxyNode
from account_pool.config import Settings
from account_pool.ports import ProxyProfileRepository
from account_pool.proxy_gateways import ProxyGatewayService
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

    async def update_proxy_gateway_configuration(self, *, content: str | None = None, url: str | None = None):
        return await self.gateway.update_configuration(content=content, url=url)


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
