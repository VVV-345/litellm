"""测试代理网关编排层：网关列表、切换、名单同步与未配置占位行为。"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Final, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import pytest
import yaml
from account_pool.api import create_router
from account_pool.clash import ClashDelayResult, ClashError, ClashProxyNode
from account_pool.config import Settings
from account_pool.domain import (
    AuthorizationFlow,
    CreateEnvironmentRequest,
    EnvironmentRecord,
    EnvironmentStatus,
    ProxyMode,
    utc_now,
)
from account_pool.ports import AuthorizationStart
from account_pool.proxy_catalog import (
    ISPProxyCatalogProfile,
    ISPProxyProfile,
    ISPSubscriptionNodeProfile,
    ProxyConfigurationProfile,
)
from account_pool.proxy_catalog_repository import ProxyCatalogRepository
from account_pool.proxy_gateways import GatewayConfigurationView, GatewayDelayView, GatewayView, ProxyGatewayService
from account_pool.result import Success
from account_pool.secrets import EnvironmentSecretDeriver
from account_pool.service import EnvironmentService
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_account_pool import FakeCLIProxy, FakeRuntime, MemoryRepository, _fake_channels, _record


@dataclass
class FakeController:
    nodes: tuple[ClashProxyNode, ...] = ()
    current: dict[str, str] = field(default_factory=dict)
    switched: list[tuple[str, str]] = field(default_factory=list)
    fail_current: bool = False
    delay_results: dict[str, ClashDelayResult] = field(default_factory=dict)
    gateway_delay_results: dict[str, ClashDelayResult] = field(default_factory=dict)
    measured: list[str] = field(default_factory=list)
    gateway_measured: list[str] = field(default_factory=list)
    change_during_measurement: tuple[str, str] | None = None
    reloaded: list[str] = field(default_factory=list)
    fail_reload: bool = False

    async def list_nodes(self) -> tuple[ClashProxyNode, ...]:
        return self.nodes

    async def selector_current(self, selector_name: str) -> str | None:
        if self.fail_current:
            raise ClashError("unreachable")
        return self.current.get(selector_name)

    async def switch_selector(self, selector_name: str, node_name: str) -> None:
        self.switched.append((selector_name, node_name))
        self.current[selector_name] = node_name

    async def selector_currents(self, selector_names: tuple[str, ...]) -> tuple[str | None, ...]:
        if self.fail_current:
            raise ClashError("unreachable")
        return tuple(self.current.get(name) for name in selector_names)

    async def measure_delay(self, node_name: str) -> ClashDelayResult:
        self.measured.append(node_name)
        if self.change_during_measurement is not None:
            selector, node = self.change_during_measurement
            self.current[selector] = node
        return self.delay_results.get(node_name, ClashDelayResult("error"))

    async def measure_gateway_delay(self, proxy_url: str) -> ClashDelayResult:
        self.gateway_measured.append(proxy_url)
        return self.gateway_delay_results.get(proxy_url, ClashDelayResult("error"))

    async def reload_config(self, path: str) -> None:
        self.reloaded.append(path)
        if self.fail_reload:
            raise ClashError("reload failed")


@dataclass
class FakeProfiles:
    upserts: list[tuple[str, str, str]] = field(default_factory=list)
    urls: dict[str, str] = field(default_factory=dict)

    async def list(self):
        return ()

    async def get_url(self, profile_id: str):
        return self.urls.get(profile_id)

    async def upsert_gateways(self, gateways) -> int:
        self.upserts.extend(gateways)
        return len(gateways)


@dataclass
class FakeCatalog:
    profiles: tuple[ISPProxyCatalogProfile, ...]
    bindings: dict[int, UUID] = field(default_factory=dict)
    fronts: dict[int, str] = field(default_factory=dict)
    configurations: dict[UUID, ProxyConfigurationProfile] = field(default_factory=dict)
    active_configuration_id: UUID | None = None

    async def get_isp(self, profile_id: UUID) -> ISPProxyCatalogProfile | None:
        return next((profile for profile in self.profiles if profile.id == profile_id), None)

    async def list_isp_bindings(self) -> tuple[tuple[int, UUID], ...]:
        return tuple(sorted(self.bindings.items()))

    async def bind_isp(self, port: int, profile_id: UUID) -> None:
        self.bindings[port] = profile_id

    async def unbind_isp(self, port: int) -> None:
        self.bindings.pop(port, None)

    async def list_front_bindings(self) -> tuple[tuple[int, str], ...]:
        return tuple(sorted(self.fronts.items()))

    async def bind_front_node(self, port: int, node_name: str | None) -> None:
        if node_name is None:
            self.fronts.pop(port, None)
            return
        self.fronts[port] = node_name

    async def list_inactive_isp_bindings(self, today: date) -> tuple[tuple[int, UUID], ...]:
        return tuple(
            (port, profile_id)
            for port, profile_id in self.bindings.items()
            for profile in self.profiles
            if profile.id == profile_id
            and (not profile.enabled or isinstance(profile, ISPProxyProfile) and profile.expired(today))
        )

    async def list_configuration_views(self) -> tuple[tuple[UUID, str, datetime, bool], ...]:
        return tuple(
            (profile.id, profile.name, profile.created_at, profile.id == self.active_configuration_id)
            for profile in self.configurations.values()
        )

    async def get_configuration(self, profile_id: UUID) -> ProxyConfigurationProfile | None:
        return self.configurations.get(profile_id)

    async def save_configuration(
        self, name: str, content: str, source_url: str | None
    ) -> ProxyConfigurationProfile:
        profile: Final = ProxyConfigurationProfile(
            id=uuid4(), name=name, content=content, source_url=source_url, created_at=datetime.now(timezone.utc)
        )
        self.configurations[profile.id] = profile
        return profile

    async def activate_configuration(self, profile_id: UUID) -> bool:
        if profile_id not in self.configurations:
            return False
        self.active_configuration_id = profile_id
        return True


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


def _environment_service(
    record: EnvironmentRecord, controller: FakeController, upstream: FakeCLIProxy | None = None
) -> EnvironmentService:
    settings: Final = _settings((7891, 7892))
    gateway, profiles = _service(settings, controller, FakeProfiles(urls={
        "clash-gateway-7891": "http://proxy.example:7891",
        "clash-gateway-7892": "http://proxy.example:7892",
    }))
    runtime: Final = FakeRuntime()
    cli: Final = upstream or FakeCLIProxy()
    return EnvironmentService(
        settings,
        MemoryRepository(record),
        runtime,
        cli,
        profiles,
        EnvironmentSecretDeriver("s" * 32),
        channels=_fake_channels(runtime, cli),
        proxy_gateways=gateway,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", (EnvironmentStatus.PROVISIONING, EnvironmentStatus.AWAITING_AUTHORIZATION, EnvironmentStatus.VALIDATING)
)
async def test_switch_rejects_gateway_used_by_pending_oauth_before_changing_clash(state: EnvironmentStatus) -> None:
    record: Final = _record(status=state).model_copy(
        update={
            "proxy_mode": ProxyMode.PROFILE,
            "proxy_profile_id": "clash-gateway-7891",
            "oauth_state": "pending-oauth",
            "oauth_expires_at": utc_now() + timedelta(minutes=5),
        }
    )
    controller: Final = FakeController(current={"clash-gateway-7891": "US01"})
    service: Final = _environment_service(record, controller)

    with pytest.raises(ClashError, match="authorization"):
        await service.switch_proxy_gateway(7891, "US02")

    assert controller.switched == []
    assert controller.current["clash-gateway-7891"] == "US01"
    assert (await service.switch_proxy_gateway(7892, "US02")).current_node == "US02"


@pytest.mark.asyncio
@pytest.mark.parametrize("finished", ("ready", "expired", "cancelled", "direct"))
async def test_switch_allows_gateway_when_oauth_is_not_pending(finished: str) -> None:
    record: Final = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION).model_copy(
        update={
            "proxy_mode": ProxyMode.PROFILE,
            "proxy_profile_id": "clash-gateway-7891",
            "status": EnvironmentStatus.READY if finished == "ready" else EnvironmentStatus.AWAITING_AUTHORIZATION,
            "authorization_flow": AuthorizationFlow.DIRECT_CREDENTIAL if finished == "direct" else AuthorizationFlow.BROWSER_OAUTH,
            "oauth_state": None if finished == "cancelled" else "pending-oauth",
            "oauth_expires_at": utc_now() + timedelta(minutes=-1 if finished == "expired" else 5),
        }
    )
    controller: Final = FakeController()
    service: Final = _environment_service(record, controller)

    assert (await service.switch_proxy_gateway(7891, "US02")).current_node == "US02"
    assert controller.switched == [("clash-gateway-7891", "US02")]


@pytest.mark.asyncio
async def test_isp_chain_uses_independent_fronts_and_preserves_subscription_config(tmp_path: Path) -> None:
    manual: Final = ISPProxyProfile(
        id=uuid4(), name="manual ISP", protocol="http", host="manual.example", port=8080,
        username="manual-user", password="manual-secret",
    )
    subscription: Final = ISPSubscriptionNodeProfile(
        id=uuid4(),
        name="subscription ISP",
        proxy_type="vless",
        config={
            "name": "subscription ISP",
            "type": "vless",
            "server": "subscription.example",
            "port": 443,
            "uuid": "00000000-0000-0000-0000-000000000003",
            "tls": {"enabled": True, "server_name": "subscription.example"},
            "packet-encoding": "xudp",
        },
    )
    catalog: Final = FakeCatalog((manual, subscription))
    groups: Final = tuple(
        {
            "name": f"clash-gateway-{port}",
            "type": "select",
            "proxies": [f"airport-{index}", "REJECT"],
        }
        for index, port in enumerate((7891, 7892, 7893), start=1)
    )
    proxies: Final = tuple(
        {"name": f"airport-{index}", "type": "socks5", "server": f"airport-{index}.example", "port": 443}
        for index in range(1, 4)
    )
    original_config: Final = {"listeners": [], "proxy-groups": groups, "proxies": proxies}
    config_path: Final = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(original_config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    settings: Final = _settings((7891, 7892, 7893)).model_copy(
        update={"clash_config_path": str(config_path)}
    )
    controller: Final = FakeController(
        nodes=(
            *(ClashProxyNode(f"airport-{index}", "Socks5") for index in range(1, 4)),
            ClashProxyNode(f"account-pool-isp-7892-{subscription.id.hex}", "VLESS"),
            ClashProxyNode("account-pool-front-7892", "Selector"),
            ClashProxyNode("airport-pool", "Selector"),
            ClashProxyNode("DIRECT", "Direct"),
            ClashProxyNode("REJECT", "Reject"),
        ),
        current={f"clash-gateway-{port}": "REJECT" for port in (7891, 7892, 7893)},
    )
    service: Final = ProxyGatewayService(
        settings, FakeProfiles(), controller, catalog=cast(ProxyCatalogRepository, catalog),
    )
    bind_front: Final = getattr(service, "bind_front_node", None)
    assert callable(bind_front)
    assert tuple(node.name for node in await service.list_nodes()) == ("airport-1", "airport-2", "airport-3")

    first_isp: Final = await service.bind_isp_gateway(7891, manual.id)
    assert first_isp.current_node == ProxyGatewayService.isp_node_name(manual.id, 7891)
    first: Final = await cast(Callable[[int, str | None], Awaitable[GatewayView]], bind_front)(7891, "airport-1")
    second_isp: Final = await service.bind_isp_gateway(7892, manual.id)
    assert second_isp.current_node == ProxyGatewayService.isp_node_name(manual.id, 7892)
    second: Final = await cast(Callable[[int, str | None], Awaitable[GatewayView]], bind_front)(7892, "airport-2")
    third_isp: Final = await service.bind_isp_gateway(7893, subscription.id)
    assert third_isp.current_node == ProxyGatewayService.isp_node_name(subscription.id, 7893)
    third: Final = await cast(Callable[[int, str | None], Awaitable[GatewayView]], bind_front)(7893, "airport-3")

    current: Final = await service.list_gateways()
    assert tuple(getattr(view, "front_node_name", None) for view in current) == (
        "airport-1", "airport-2", "airport-3",
    )
    assert first.current_node == ProxyGatewayService.isp_node_name(manual.id, 7891)
    assert second.current_node == ProxyGatewayService.isp_node_name(manual.id, 7892)
    assert third.current_node == ProxyGatewayService.isp_node_name(subscription.id, 7893)
    updated_config: Final = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    updated_proxies: Final = {
        entry["name"]: entry for entry in updated_config["proxies"] if isinstance(entry, dict)
    }
    updated_groups: Final = {
        entry["name"]: entry for entry in updated_config["proxy-groups"] if isinstance(entry, dict)
    }
    assert updated_proxies[ProxyGatewayService.isp_node_name(manual.id, 7891)] == {
        "name": ProxyGatewayService.isp_node_name(manual.id, 7891),
        "type": "http",
        "server": "manual.example",
        "port": 8080,
        "username": "manual-user",
        "password": "manual-secret",
        "dialer-proxy": "account-pool-front-7891",
    }
    assert updated_proxies[ProxyGatewayService.isp_node_name(manual.id, 7892)]["dialer-proxy"] == "account-pool-front-7892"
    assert updated_proxies[ProxyGatewayService.isp_node_name(subscription.id, 7893)] == {
        **subscription.config,
        "name": ProxyGatewayService.isp_node_name(subscription.id, 7893),
        "dialer-proxy": "account-pool-front-7893",
    }
    assert updated_groups["clash-gateway-7891"]["proxies"] == [
        ProxyGatewayService.isp_node_name(manual.id, 7891), "REJECT",
    ]
    assert updated_groups["account-pool-front-7891"]["proxies"] == [
        "airport-1", "airport-2", "airport-3", "REJECT",
    ]
    assert catalog.bindings == {7891: manual.id, 7892: manual.id, 7893: subscription.id}

    controller.nodes = (
        *(ClashProxyNode(f"airport-{index}", "Socks5") for index in range(1, 5)),
        ClashProxyNode(f"account-pool-isp-7892-{subscription.id.hex}", "VLESS"),
        ClashProxyNode("account-pool-front-7892", "Selector"),
    )
    await service.update_configuration(
        content=yaml.safe_dump(
            {
                "listeners": [],
                "proxy-groups": [],
                "proxies": [
                    {
                        "name": f"airport-{index}",
                        "type": "socks5",
                        "server": f"airport-{index}.example",
                        "port": 443,
                    }
                    for index in range(1, 5)
                ],
            },
            sort_keys=False,
        )
    )
    replaced_config: Final = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    replaced_groups: Final = {
        entry["name"]: entry for entry in replaced_config["proxy-groups"] if isinstance(entry, dict)
    }
    assert catalog.fronts == {7891: "airport-1", 7892: "airport-2", 7893: "airport-3"}
    assert replaced_groups["account-pool-front-7891"]["proxies"] == [
        "airport-1", "airport-2", "airport-3", "airport-4", "REJECT",
    ]
    assert replaced_groups["clash-gateway-7891"]["proxies"] == [
        ProxyGatewayService.isp_node_name(manual.id, 7891), "REJECT",
    ]


@pytest.mark.asyncio
async def test_missing_front_or_subscription_isp_blocks_then_recovers_same_binding(tmp_path: Path) -> None:
    subscription: Final = ISPSubscriptionNodeProfile(
        id=uuid4(),
        name="subscription ISP",
        proxy_type="vless",
        config={
            "name": "subscription ISP",
            "type": "vless",
            "server": "subscription.example",
            "port": 443,
            "uuid": "00000000-0000-0000-0000-000000000003",
        },
    )
    catalog: Final = FakeCatalog((subscription,), bindings={7891: subscription.id}, fronts={7891: "airport-a"})
    config_path: Final = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "listeners": [{"name": "gateway-7891", "port": 7891, "proxy": "clash-gateway-7891"}],
                "proxy-groups": [{"name": "clash-gateway-7891", "type": "select", "proxies": ["REJECT"]}],
                "proxies": [{"name": "airport-a", "type": "socks5", "server": "airport.example", "port": 443}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    controller: Final = FakeController(
        nodes=(ClashProxyNode("airport-a", "Socks5"),),
        current={"clash-gateway-7891": "REJECT"},
    )
    service: Final = ProxyGatewayService(
        _settings((7891,)).model_copy(update={"clash_config_path": str(config_path)}),
        FakeProfiles(),
        controller,
        catalog=cast(ProxyCatalogRepository, catalog),
    )

    await service.reconcile_isp_bindings(date(2026, 10, 22))
    chain_name: Final = ProxyGatewayService.isp_node_name(subscription.id, 7891)
    assert controller.current["clash-gateway-7891"] == chain_name

    controller.nodes = ()
    await service.reconcile_isp_bindings(date(2026, 10, 22))
    blocked_config: Final = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    blocked_group: Final = next(group for group in blocked_config["proxy-groups"] if group["name"] == "clash-gateway-7891")
    assert blocked_group["proxies"] == ["REJECT"]
    assert controller.current["clash-gateway-7891"] == "REJECT"
    assert catalog.bindings == {7891: subscription.id}
    assert catalog.fronts == {7891: "airport-a"}

    controller.nodes = (ClashProxyNode("airport-a", "Socks5"),)
    await service.reconcile_isp_bindings(date(2026, 10, 22))
    assert controller.current["clash-gateway-7891"] == chain_name

    catalog.profiles = ()
    await service.reconcile_isp_bindings(date(2026, 10, 22))
    blocked_again: Final = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    blocked_again_group: Final = next(
        group for group in blocked_again["proxy-groups"] if group["name"] == "clash-gateway-7891"
    )
    assert blocked_again_group["proxies"] == ["REJECT"]
    assert controller.current["clash-gateway-7891"] == "REJECT"

    catalog.profiles = (subscription,)
    await service.reconcile_isp_bindings(date(2026, 10, 22))
    assert controller.current["clash-gateway-7891"] == chain_name


@pytest.mark.asyncio
async def test_isp_gateway_selector_cannot_inherit_provider_or_include_all_choices(tmp_path: Path) -> None:
    manual: Final = ISPProxyProfile(
        id=uuid4(), name="manual ISP", protocol="http", host="manual.example", port=8080,
    )
    catalog: Final = FakeCatalog((manual,), bindings={7891: manual.id}, fronts={7891: "airport-a"})
    config_path: Final = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "listeners": [],
                "proxy-groups": [
                    {
                        "name": "clash-gateway-7891",
                        "type": "select",
                        "proxies": ["airport-a", "REJECT"],
                        "use": ["airport-provider"],
                        "include-all": True,
                    }
                ],
                "proxies": [{"name": "airport-a", "type": "socks5", "server": "airport.example", "port": 443}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    controller: Final = FakeController(nodes=(ClashProxyNode("airport-a", "Socks5"),))
    service: Final = ProxyGatewayService(
        _settings((7891,)).model_copy(update={"clash_config_path": str(config_path)}),
        FakeProfiles(),
        controller,
        catalog=cast(ProxyCatalogRepository, catalog),
    )

    await service.reconcile_isp_bindings(date(2026, 10, 22))

    config: Final = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    gateway_group: Final = next(group for group in config["proxy-groups"] if group["name"] == "clash-gateway-7891")
    assert gateway_group["proxies"] == [
        ProxyGatewayService.isp_node_name(manual.id, 7891), "REJECT",
    ]
    assert "use" not in gateway_group
    assert gateway_group.get("include-all") is not True


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription", (False, True))
async def test_clearing_front_connects_to_isp_directly_and_can_restore_front(tmp_path: Path, subscription: bool) -> None:
    profile: Final = (
        ISPSubscriptionNodeProfile(
            id=uuid4(), name="ISP", proxy_type="socks5",
            config={"name": "ISP", "type": "socks5", "server": "isp.example", "port": 1080,
                    "username": "user", "password": "secret", "dialer-proxy": "old-front"},
        )
        if subscription else ISPProxyProfile(
            id=uuid4(), name="ISP", protocol="socks5", host="isp.example", port=1080,
            username="user", password="secret",
        )
    )
    catalog: Final = FakeCatalog((profile,))
    config_path: Final = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({
        "listeners": [], "proxy-groups": [],
        "proxies": [{"name": "airport-a", "type": "socks5", "server": "front.example", "port": 1080}],
    }), encoding="utf-8")
    controller: Final = FakeController(nodes=(ClashProxyNode("airport-a", "Socks5"),))
    service: Final = ProxyGatewayService(
        _settings((7891,)).model_copy(update={"clash_config_path": str(config_path)}),
        FakeProfiles(), controller, catalog=cast(ProxyCatalogRepository, catalog),
    )
    await service.bind_front_node(7891, "airport-a")
    await service.bind_isp_gateway(7891, profile.id)
    direct: Final = await service.bind_front_node(7891, None)
    config: Final = yaml.safe_load(config_path.read_text())
    node: Final = next(p for p in config["proxies"] if p["name"] == direct.current_node)
    assert node["server"] == "isp.example"
    assert node["port"] == 1080
    assert node["password"] == "secret"
    assert node["dialer-proxy"] == "DIRECT"
    assert catalog.fronts == {}
    assert direct.front_node_name is None
    await service.reconcile_isp_bindings(date.today())
    assert controller.current["clash-gateway-7891"] == direct.current_node
    restored: Final = await service.bind_front_node(7891, "airport-a")
    restored_config: Final = yaml.safe_load(config_path.read_text())
    restored_node: Final = next(p for p in restored_config["proxies"] if p["name"] == restored.current_node)
    assert restored_node["dialer-proxy"] == "account-pool-front-7891"
    assert restored.front_node_name == "airport-a"


@pytest.mark.asyncio
async def test_front_binding_reload_failure_restores_config_and_catalog_binding(tmp_path: Path) -> None:
    manual: Final = ISPProxyProfile(
        id=uuid4(), name="manual ISP", protocol="http", host="manual.example", port=8080,
    )
    catalog: Final = FakeCatalog((manual,))
    config_path: Final = tmp_path / "config.yaml"
    original_config: Final = {
        "listeners": [],
        "proxy-groups": [{"name": "clash-gateway-7891", "type": "select", "proxies": ["REJECT"]}],
        "proxies": [{"name": "airport-a", "type": "socks5", "server": "airport.example", "port": 443}],
    }
    original: Final = yaml.safe_dump(original_config, sort_keys=False)
    config_path.write_text(original, encoding="utf-8")
    controller: Final = FakeController(nodes=(ClashProxyNode("airport-a", "Socks5"),))
    service: Final = ProxyGatewayService(
        _settings((7891,)).model_copy(update={"clash_config_path": str(config_path)}),
        FakeProfiles(),
        controller,
        catalog=cast(ProxyCatalogRepository, catalog),
    )
    await service.bind_isp_gateway(7891, manual.id)
    before_front_binding: Final = config_path.read_text(encoding="utf-8")
    controller.fail_reload = True

    with pytest.raises(ClashError, match="configuration update"):
        await service.bind_front_node(7891, "airport-a")

    assert config_path.read_text(encoding="utf-8") == before_front_binding
    assert catalog.fronts == {}
    assert controller.current["clash-gateway-7891"] == ProxyGatewayService.isp_node_name(manual.id, 7891)


@pytest.mark.asyncio
async def test_removing_gateway_cleans_chain_front_bindings_and_managed_config(tmp_path: Path) -> None:
    manual: Final = ISPProxyProfile(
        id=uuid4(), name="manual ISP", protocol="http", host="manual.example", port=8080,
    )
    catalog: Final = FakeCatalog((manual,), bindings={7891: manual.id}, fronts={7891: "airport-a"})
    chain_name: Final = ProxyGatewayService.isp_node_name(manual.id, 7891)
    config_path: Final = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "listeners": [{"name": "gateway-7891", "port": 7891, "proxy": "clash-gateway-7891"}],
                "proxy-groups": [
                    {"name": "clash-gateway-7891", "type": "select", "proxies": [chain_name, "REJECT"]},
                    {"name": "account-pool-front-7891", "type": "select", "proxies": ["airport-a", "REJECT"]},
                ],
                "proxies": [
                    {"name": "airport-a", "type": "socks5", "server": "airport.example", "port": 443},
                    {
                        "name": chain_name,
                        "type": "http",
                        "server": "manual.example",
                        "port": 8080,
                        "dialer-proxy": "account-pool-front-7891",
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    @dataclass
    class DeletableProfiles(FakeProfiles):
        deleted: list[str] = field(default_factory=list)

        async def delete_gateway(self, profile_id: str) -> bool:
            self.deleted.append(profile_id)
            return True

    class RegisteredGatewayService(ProxyGatewayService):
        ports: tuple[int, ...] = (7891,)

        async def _registered_ports(self) -> tuple[int, ...]:
            return self.ports

        async def _unregister_port(self, port: int) -> None:
            self.ports = tuple(registered for registered in self.ports if registered != port)

        async def _register_port(self, port: int) -> None:
            self.ports = tuple(sorted((*self.ports, port)))

    profiles: Final = DeletableProfiles()
    service: Final = RegisteredGatewayService(
        _settings((7891,)).model_copy(update={"clash_config_path": str(config_path)}),
        profiles,
        FakeController(),
        catalog=cast(ProxyCatalogRepository, catalog),
    )

    await service.remove_gateway(7891, frozenset())

    removed_config: Final = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert removed_config["listeners"] == []
    assert removed_config["proxy-groups"] == []
    assert all(proxy.get("name") != chain_name for proxy in removed_config["proxies"])
    assert catalog.bindings == {}
    assert catalog.fronts == {}
    assert service.ports == ()
    assert profiles.deleted == ["clash-gateway-7891"]


@pytest.mark.asyncio
@pytest.mark.parametrize("compensation_fails", (False, True))
async def test_removing_last_gateway_restores_config_when_profile_deletion_fails(
    tmp_path: Path, compensation_fails: bool
) -> None:
    manual: Final = ISPProxyProfile(id=uuid4(), name="manual", protocol="http", host="isp.example", port=8080)

    class ConstrainedCatalog(FakeCatalog):
        async def bind_isp(self, port: int, profile_id: UUID) -> None:
            if port not in service.ports:
                raise RuntimeError("gateway foreign key violation")
            if compensation_fails:
                raise RuntimeError("binding restoration unavailable")
            await super().bind_isp(port, profile_id)

        async def bind_front_node(self, port: int, node_name: str | None) -> None:
            if node_name is not None and port not in service.ports:
                raise RuntimeError("gateway foreign key violation")
            await super().bind_front_node(port, node_name)

    catalog: Final = ConstrainedCatalog((manual,), bindings={7891: manual.id}, fronts={7891: "airport-a"})
    config_path: Final = tmp_path / "config.yaml"
    original_config: Final = {
        "listeners": [{"name": "gateway-7891", "port": 7891, "proxy": "clash-gateway-7891"}],
        "proxy-groups": [
            {"name": "clash-gateway-7891", "type": "select", "proxies": ["airport-a", "REJECT"]},
            {"name": "account-pool-front-7891", "type": "select", "proxies": ["airport-a", "REJECT"]},
        ],
        "proxies": [{"name": "airport-a", "type": "socks5", "server": "airport.example", "port": 443}],
    }
    original: Final = yaml.safe_dump(original_config, sort_keys=False)
    config_path.write_text(original, encoding="utf-8")

    @dataclass
    class FailingProfiles(FakeProfiles):
        async def delete_gateway(self, profile_id: str) -> bool:
            raise RuntimeError("database unavailable")

    class RegisteredGatewayService(ProxyGatewayService):
        ports: tuple[int, ...] = (7891,)

        async def _registered_ports(self) -> tuple[int, ...]:
            return self.ports

        async def _unregister_port(self, port: int) -> None:
            self.ports = tuple(registered for registered in self.ports if registered != port)

        async def _register_port(self, port: int) -> None:
            self.ports = tuple(sorted((*self.ports, port)))

    service: Final = RegisteredGatewayService(
        _settings((7891,)).model_copy(update={"clash_config_path": str(config_path)}),
        FailingProfiles(),
        FakeController(),
        catalog=cast(ProxyCatalogRepository, catalog),
    )

    with pytest.raises(ClashError, match="deletion failed"):
        await service.remove_gateway(7891, frozenset())

    assert config_path.read_text(encoding="utf-8") == original
    assert service.ports == (7891,)
    assert catalog.bindings == ({} if compensation_fails else {7891: manual.id})
    assert catalog.fronts == ({} if compensation_fails else {7891: "airport-a"})


@pytest.mark.asyncio
@pytest.mark.parametrize("front", (None, "airport-a"))
async def test_gateway_probe_uses_bound_listener_and_rejects_unbound_gateway(front: str | None) -> None:
    from datetime import datetime, timezone

    from account_pool.isp_probe import GatewayISPProbe, GatewayISPProbeResult

    manual: Final = ISPProxyProfile(
        id=uuid4(), name="manual ISP", protocol="http", host="manual.example", port=8080,
    )

    class Probe:
        def __init__(self) -> None:
            self.calls: list[tuple[int, str]] = []

        async def probe(self, port: int, proxy_url: str) -> GatewayISPProbeResult:
            self.calls.append((port, proxy_url))
            return GatewayISPProbeResult(
                port=port,
                checked_at=datetime.now(timezone.utc),
                ip_status="ok",
                exit_ip="8.8.8.8",
                openai_status="response",
                openai_http_status=401,
            )

    gateway_probe: Final = Probe()
    chain_name: Final = ProxyGatewayService.isp_node_name(manual.id, 7891)
    controller: Final = FakeController(
        nodes=(ClashProxyNode("airport-a", "Socks5"),),
        current={"clash-gateway-7891": chain_name, **({"account-pool-front-7891": front} if front else {})},
    )
    catalog: Final = FakeCatalog((manual,), bindings={7891: manual.id}, fronts={7891: front} if front else {})
    service: Final = ProxyGatewayService(
        _settings((7891,)),
        FakeProfiles(),
        controller,
        catalog=cast(ProxyCatalogRepository, catalog),
        gateway_probe=cast(GatewayISPProbe, gateway_probe),
    )

    result: Final = await service.probe_gateway(7891)
    assert result.openai_http_status == 401
    assert gateway_probe.calls == [(7891, "http://host.docker.internal:7891")]

    unbound_catalog: Final = FakeCatalog((manual,))
    unbound_probe: Final = Probe()
    unbound_service: Final = ProxyGatewayService(
        _settings((7891,)),
        FakeProfiles(),
        controller,
        catalog=cast(ProxyCatalogRepository, unbound_catalog),
        gateway_probe=cast(GatewayISPProbe, unbound_probe),
    )
    with pytest.raises(ClashError, match="ISP chain"):
        await unbound_service.probe_gateway(7891)
    assert unbound_probe.calls == []


def test_manager_front_binding_route_uses_separate_front_endpoint() -> None:
    from datetime import datetime, timezone

    from account_pool.isp_probe import GatewayISPProbeResult

    calls: Final[list[tuple[str, int, str | None]]] = []

    class Service:
        async def bind_proxy_gateway_front(self, port: int, node_name: str | None) -> GatewayView:
            calls.append(("front", port, node_name))
            return GatewayView(
                port=port,
                profile_id=f"clash-gateway-{port}",
                name=f"Clash 端口 {port}",
                proxy_url=f"http://host.docker.internal:{port}",
                current_node="account-pool-isp-7891-chain",
                isp_profile_id=uuid4(),
                front_node_name=node_name,
            )

        async def probe_proxy_gateway_isp(self, port: int) -> GatewayISPProbeResult:
            calls.append(("probe", port, None))
            return GatewayISPProbeResult(
                port=port,
                checked_at=datetime.now(timezone.utc),
                ip_status="ok",
                exit_ip="8.8.8.8",
                openai_status="response",
                openai_http_status=401,
            )

    app: Final = FastAPI()
    app.include_router(create_router(cast(EnvironmentService, Service()), "t" * 32))

    with TestClient(app) as client:
        unauthorized: Final = client.put("/api/proxy-gateways/7891/front", json={"node_name": "airport-a"})
        bound: Final = client.put(
            "/api/proxy-gateways/7891/front",
            headers={"Authorization": "Bearer " + "t" * 32},
            json={"node_name": "airport-a"},
        )
        probed: Final = client.post(
            "/api/proxy-gateways/7891/probe", headers={"Authorization": "Bearer " + "t" * 32}
        )

    assert unauthorized.status_code == 401
    assert bound.status_code == 200
    assert bound.json()["front_node_name"] == "airport-a"
    assert probed.status_code == 200
    assert probed.json()["openai_http_status"] == 401
    assert calls == [("front", 7891, "airport-a"), ("probe", 7891, None)]


@dataclass
class BlockingController(FakeController):
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def switch_selector(self, selector_name: str, node_name: str) -> None:
        if selector_name == "clash-gateway-7891":
            self.entered.set()
            await self.release.wait()
        await super().switch_selector(selector_name, node_name)


class BlockingAuthorizationCLI(FakeCLIProxy):
    def __init__(self) -> None:
        super().__init__()
        self.entered: Final = asyncio.Event()
        self.release: Final = asyncio.Event()

    async def start_authorization(self, record: EnvironmentRecord) -> AuthorizationStart:
        if record.proxy_profile_id == "clash-gateway-7891":
            self.entered.set()
            await self.release.wait()
        return await super().start_authorization(record)


@pytest.mark.asyncio
@pytest.mark.parametrize("reauthorize", (False, True))
async def test_gateway_switch_finishes_before_same_profile_oauth_starts(reauthorize: bool) -> None:
    record: Final = _record(status=EnvironmentStatus.READY).model_copy(
        update={"proxy_mode": ProxyMode.PROFILE, "proxy_profile_id": "clash-gateway-7891"}
    )
    controller: Final = BlockingController()
    cli: Final = FakeCLIProxy()
    service: Final = _environment_service(record, controller, cli)
    switching: Final = asyncio.create_task(service.switch_proxy_gateway(7891, "US02"))
    await asyncio.wait_for(controller.entered.wait(), 1)
    starting: Final = asyncio.create_task(
        service.authorize_environment(record.id) if reauthorize else service.create_environment(
            CreateEnvironmentRequest(name="same gateway", proxy_profile_id="clash-gateway-7891")
        )
    )
    try:
        await asyncio.sleep(0)
        assert not starting.done()
        assert "oauth" not in cli.events
        other: Final = await asyncio.wait_for(service.create_environment(
            CreateEnvironmentRequest(name="other gateway", proxy_profile_id="clash-gateway-7892")
        ), 1)
        assert isinstance(other, Success)
        controller.release.set()
        assert (await switching).current_node == "US02"
        assert isinstance(await starting, Success)
    finally:
        controller.release.set()
        await asyncio.gather(switching, starting, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("reauthorize", (False, True))
async def test_oauth_start_persists_state_before_same_gateway_switch_check(reauthorize: bool) -> None:
    record: Final = _record(status=EnvironmentStatus.READY).model_copy(
        update={"proxy_mode": ProxyMode.PROFILE, "proxy_profile_id": "clash-gateway-7891"}
    )
    controller: Final = FakeController(current={"clash-gateway-7891": "US01"})
    cli: Final = BlockingAuthorizationCLI()
    service: Final = _environment_service(record, controller, cli)
    starting: Final = asyncio.create_task(
        service.authorize_environment(record.id) if reauthorize else service.create_environment(
            CreateEnvironmentRequest(name="same gateway", proxy_profile_id="clash-gateway-7891")
        )
    )
    await asyncio.wait_for(cli.entered.wait(), 1)
    switching: Final = asyncio.create_task(service.switch_proxy_gateway(7891, "US02"))
    try:
        await asyncio.sleep(0)
        assert not switching.done()
        assert (await asyncio.wait_for(service.switch_proxy_gateway(7892, "US02"), 1)).current_node == "US02"
        cli.release.set()
        assert isinstance(await starting, Success)
        with pytest.raises(ClashError, match="authorization"):
            await switching
        assert controller.current["clash-gateway-7891"] == "US01"
    finally:
        cli.release.set()
        await asyncio.gather(starting, switching, return_exceptions=True)


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


@pytest.mark.asyncio
async def test_measure_delays_deduplicates_nodes_and_keeps_individual_failures() -> None:
    controller: Final = FakeController(
        current={"clash-gateway-7891": "US01", "clash-gateway-7892": "US01", "clash-gateway-7893": "US02"},
        delay_results={"US01": ClashDelayResult("ok", 183), "US02": ClashDelayResult("timeout")},
        gateway_delay_results={
            "http://host.docker.internal:7891": ClashDelayResult("ok", 193),
            "http://host.docker.internal:7892": ClashDelayResult("ok", 197),
            "http://host.docker.internal:7893": ClashDelayResult("timeout"),
        },
    )
    service, _ = _service(_settings((7891, 7892, 7893, 7894)), controller)

    views: Final = await service.measure_delays()

    assert controller.measured == ["US01", "US02"]
    assert tuple((view.port, view.current_node, view.status, view.delay_ms) for view in views) == (
        (7891, "US01", "ok", 183),
        (7892, "US01", "ok", 183),
        (7893, "US02", "timeout", None),
        (7894, None, "error", None),
    )
    assert tuple((view.port, view.gateway_status, view.gateway_delay_ms) for view in views) == (
        (7891, "ok", 193),
        (7892, "ok", 197),
        (7893, "timeout", None),
        (7894, "error", None),
    )
    assert controller.gateway_measured == [
        "http://host.docker.internal:7891",
        "http://host.docker.internal:7892",
        "http://host.docker.internal:7893",
    ]
    assert all(view.checked_at.tzinfo is not None for view in views)


@pytest.mark.asyncio
async def test_measure_delays_discards_result_if_node_changed_during_probe() -> None:
    controller: Final = FakeController(
        current={"clash-gateway-7891": "US01"},
        delay_results={"US01": ClashDelayResult("ok", 183)},
        change_during_measurement=("clash-gateway-7891", "US02"),
    )
    service, _ = _service(_settings((7891,)), controller)

    view: Final = (await service.measure_delays())[0]

    assert view.current_node == "US02"
    assert view.status == "error"
    assert view.delay_ms is None


@pytest.mark.asyncio
async def test_disabled_delays_are_empty_and_unreachable_controller_fails() -> None:
    disabled: Final = ProxyGatewayService.disabled(_settings(()), FakeProfiles())
    assert await disabled.measure_delays() == ()
    unconfigured: Final = ProxyGatewayService.disabled(_settings((7891,), ""), FakeProfiles())
    with pytest.raises(ClashError, match="not configured"):
        await unconfigured.measure_delays()
    service, _ = _service(_settings((7891,)), FakeController(fail_current=True))
    with pytest.raises(ClashError, match="unreachable"):
        await service.measure_delays()


@pytest.mark.asyncio
async def test_isp_subscription_download_reuses_public_url_guards() -> None:
    requested: list[str] = []

    async def fetch(url: str) -> str:
        requested.append(url)
        return "proxies:\n  - name: isp\n    type: ss\n    server: edge.example\n    port: 443\n"

    service: Final = ProxyGatewayService(
        _settings((7891,)), FakeProfiles(), FakeController(), fetch_configuration=fetch,
    )
    download: Final = getattr(service, "fetch_isp_subscription", None)
    assert callable(download)

    content: Final = await cast(Callable[[str], Awaitable[str]], download)("https://isp.example/subscription.yaml")

    assert "name: isp" in content
    assert requested == ["https://isp.example/subscription.yaml"]
    with pytest.raises(ClashError):
        await cast(Callable[[str], Awaitable[str]], download)("http://127.0.0.1/private.yaml")
    assert requested == ["https://isp.example/subscription.yaml"]


def _subscription_transport(
    monkeypatch: pytest.MonkeyPatch, addresses: tuple[str, ...], response: httpx.Response | Exception
) -> list[httpx.Request]:
    requests: list[httpx.Request] = []
    create_client: Final = httpx.AsyncClient

    def resolve(host, port, *args, **kwargs):
        return [
            (socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))
            for address in addresses
        ]

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if isinstance(response, Exception):
            raise response
        return response

    def client(**kwargs) -> httpx.AsyncClient:
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        assert kwargs["timeout"] == 20.0
        return create_client(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(httpx, "AsyncClient", client)
    return requests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addresses",
    (
        ("127.0.0.1",), ("10.0.0.1",), ("169.254.169.254",), ("100.64.0.1",), ("224.0.0.1",),
        ("::1",), ("fc00::1",), ("fe80::1",), ("ff02::1",),
        ("8.8.8.8", "10.0.0.1"), ("2606:4700:4700::1111", "::1"),
    ),
)
async def test_subscription_download_rejects_any_non_public_dns_answer(
    monkeypatch: pytest.MonkeyPatch, addresses: tuple[str, ...]
) -> None:
    requests: Final = _subscription_transport(monkeypatch, addresses, httpx.Response(200, text="proxies: []"))
    service, _ = _service(_settings((7891,)), FakeController())

    with pytest.raises(ClashError, match="private address"):
        await service.fetch_isp_subscription("https://subscription.example/private-token")

    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme, address", (("http", "8.8.8.8"), ("https", "2606:4700:4700::1111")))
async def test_subscription_download_pins_public_address_and_preserves_original_host(
    monkeypatch: pytest.MonkeyPatch, scheme: str, address: str
) -> None:
    requests: Final = _subscription_transport(monkeypatch, (address,), httpx.Response(200, text="proxies: []"))
    service, _ = _service(_settings((7891,)), FakeController())

    assert await service.fetch_isp_subscription(f"{scheme}://subscription.example:8443/token") == "proxies: []"
    assert len(requests) == 1
    assert requests[0].url.host == address
    assert requests[0].url.port == 8443
    assert requests[0].headers["host"] == "subscription.example:8443"
    assert requests[0].extensions["sni_hostname"] == "subscription.example"


class SubscriptionStream(httpx.AsyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = chunks
        self.consumed = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_subscription_download_stops_stream_at_byte_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    stream: Final = SubscriptionStream((b"x" * 65536,) * 32 + (b"x", b"not read"))
    _subscription_transport(monkeypatch, ("8.8.8.8",), httpx.Response(200, stream=stream))
    service, _ = _service(_settings((7891,)), FakeController())

    with pytest.raises(ClashError, match="too large"):
        await service.fetch_isp_subscription("https://subscription.example/token")

    assert stream.consumed == 33
    assert stream.closed


@pytest.mark.asyncio
async def test_subscription_download_rejects_redirect_without_reading_body(monkeypatch: pytest.MonkeyPatch) -> None:
    stream: Final = SubscriptionStream((b"not read",))
    requests: Final = _subscription_transport(
        monkeypatch, ("8.8.8.8",), httpx.Response(302, headers={"location": "http://127.0.0.1"}, stream=stream)
    )
    service, _ = _service(_settings((7891,)), FakeController())

    with pytest.raises(ClashError, match="redirects"):
        await service.fetch_isp_subscription("https://subscription.example/token")

    assert len(requests) == 1
    assert stream.consumed == 0
    assert stream.closed


@pytest.mark.asyncio
async def test_subscription_download_accepts_exact_byte_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    stream: Final = SubscriptionStream((b"x" * 65536,) * 32)
    _subscription_transport(monkeypatch, ("8.8.8.8",), httpx.Response(200, stream=stream))
    service, _ = _service(_settings((7891,)), FakeController())

    content: Final = await service.fetch_isp_subscription("http://subscription.example/token")

    assert len(content) == 2097152
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("status", (401, 404, 500))
async def test_subscription_download_rejects_failed_status_without_reading_body(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    stream: Final = SubscriptionStream((b"private provider error",))
    _subscription_transport(monkeypatch, ("8.8.8.8",), httpx.Response(status, stream=stream))
    service, _ = _service(_settings((7891,)), FakeController())

    with pytest.raises(ClashError, match="^proxy configuration download failed$"):
        await service.fetch_isp_subscription("https://subscription.example/token")

    assert stream.consumed == 0
    assert stream.closed


@pytest.mark.asyncio
async def test_subscription_download_sanitizes_transport_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    _subscription_transport(
        monkeypatch, ("8.8.8.8",), httpx.ConnectError("https://subscription.example/private-token")
    )
    service, _ = _service(_settings((7891,)), FakeController())

    with pytest.raises(ClashError, match="^proxy configuration download failed$"):
        await service.fetch_isp_subscription("https://subscription.example/private-token")


@pytest.mark.asyncio
async def test_subscription_download_sanitizes_dns_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: Final = _subscription_transport(monkeypatch, (), httpx.Response(200, text="not read"))

    def resolve(*args, **kwargs):
        raise socket.gaierror("private DNS diagnostic")

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    service, _ = _service(_settings((7891,)), FakeController())

    with pytest.raises(ClashError, match="^proxy configuration download failed$"):
        await service.fetch_isp_subscription("https://subscription.example/private-token")

    assert requests == []


@dataclass(frozen=True)
class DelayService:
    gateway: ProxyGatewayService

    async def measure_proxy_gateway_delays(self) -> tuple[GatewayDelayView, ...]:
        return await self.gateway.measure_delays()


def test_manager_delay_api_requires_auth_and_returns_measured_results() -> None:
    gateway, _ = _service(
        _settings((7891,)),
        FakeController(current={"clash-gateway-7891": "US01"}, delay_results={"US01": ClashDelayResult("ok", 183)}),
    )
    app: Final = FastAPI()
    app.include_router(create_router(cast(EnvironmentService, DelayService(gateway)), "t" * 32))
    with TestClient(app) as client:
        denied: Final = client.post("/api/proxy-gateways/delay")
        response: Final = client.post("/api/proxy-gateways/delay", headers={"Authorization": "Bearer " + "t" * 32})

    assert denied.status_code == 401
    assert response.status_code == 200
    assert response.json()[0]["delay_ms"] == 183
    assert response.json()[0]["status"] == "ok"


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
