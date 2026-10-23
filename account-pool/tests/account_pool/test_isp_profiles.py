"""测试 ISP 代理导入格式和凭据展示边界。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from importlib import import_module
from typing import Final, cast
from uuid import UUID, uuid4

import pytest
from account_pool.isp_probe import ISPProbeResult
from account_pool.proxy_catalog import ISPProxyInput, ISPProxyProfile, parse_isp_import
from account_pool.proxy_catalog_repository import ProxyCatalogRepository
from account_pool.proxy_catalog_service import ProxyCatalogService
from account_pool.proxy_expiry import ExpiringProxyCatalog, ISPBoundGateways, ISPExpiryScheduler


def _subscription_parser() -> Callable[[str], tuple[object, ...]] | None:
    try:
        module: Final = import_module("account_pool.isp_subscription")
    except ModuleNotFoundError as error:
        if error.name != "account_pool.isp_subscription":
            raise
        return None
    parser: Final = getattr(module, "parse_isp_subscription", None)
    return cast(Callable[[str], tuple[object, ...]], parser) if callable(parser) else None


def test_isp_subscription_parser_preserves_complete_top_level_mihomo_nodes() -> None:
    parser: Final = _subscription_parser()
    assert parser is not None

    nodes: Final = parser(
        "proxies:\n"
        "  - name: home-vless\n"
        "    type: vless\n"
        "    server: edge.example\n"
        "    port: 443\n"
        "    uuid: 00000000-0000-0000-0000-000000000001\n"
        "    tls:\n"
        "      enabled: true\n"
        "      server_name: edge.example\n"
        "    packet-encoding: xudp\n"
        "  - name: home-ss\n"
        "    type: ss\n"
        "    server: edge-two.example\n"
        "    port: 8388\n"
        "    cipher: aes-128-gcm\n"
        "    password: do-not-return-this\n"
        "proxy-providers:\n"
        "  ignored:\n"
        "    type: http\n"
        "    url: https://provider.example/ignored.yaml\n"
    )

    assert tuple(getattr(node, "name") for node in nodes) == ("home-vless", "home-ss")
    first_config: Final = getattr(nodes[0], "config")
    assert first_config == {
        "name": "home-vless",
        "type": "vless",
        "server": "edge.example",
        "port": 443,
        "uuid": "00000000-0000-0000-0000-000000000001",
        "tls": {"enabled": True, "server_name": "edge.example"},
        "packet-encoding": "xudp",
    }
    assert "do-not-return-this" not in repr(nodes)


@pytest.mark.parametrize(
    "content",
    (
        "proxies: []",
        "proxy-providers:\n  nested:\n    proxies:\n      - name: hidden\n        type: ss\n        server: edge.example\n        port: 443\n",
        "proxies:\n  - name: duplicate\n    type: ss\n    server: edge.example\n    port: 443\n  - name: duplicate\n    type: vless\n    server: edge-two.example\n    port: 443\n",
        "proxies:\n  - name: invalid\n    type: selector\n    server: edge.example\n    port: 443\n",
        "proxies: [unterminated",
        "x" * (2 * 1024 * 1024 + 1),
    ),
    ids=("empty", "nested-provider", "duplicate", "selector", "invalid-yaml", "oversized"),
)
def test_isp_subscription_parser_rejects_invalid_documents_without_echoing_input(content: str) -> None:
    parser: Final = _subscription_parser()
    assert parser is not None

    with pytest.raises(ValueError) as error:
        parser(content)

    assert "do-not-return-this" not in str(error.value)


def test_parse_isp_import_accepts_uri_and_colon_delimited_rows() -> None:
    entries: Final = parse_isp_import(
        "socks5://user:secret@proxy.example:1234\n"
        "proxy-two.example:2345:second:pass:with:colon"
    )

    assert tuple((item.name, item.protocol, item.host, item.port, item.username, item.password) for item in entries) == (
        ("proxy.example:1234", "socks5", "proxy.example", 1234, "user", "secret"),
        ("proxy-two.example:2345", "http", "proxy-two.example", 2345, "second", "pass:with:colon"),
    )
    assert "secret" not in repr(entries)
    assert "pass:with:colon" not in repr(entries)


def test_parse_isp_import_accepts_csv_and_clash_yaml_with_expiration() -> None:
    csv_entries: Final = parse_isp_import(
        "name,protocol,host,port,username,password,expires_on\n"
        "ISP A,http,proxy.example,3456,alice,secret,2026-11-10"
    )
    clash_entries: Final = parse_isp_import(
        "proxies:\n"
        "  - name: ISP B\n"
        "    type: socks5\n"
        "    server: proxy-two.example\n"
        "    port: 4567\n"
        "    username: bob\n"
        "    password: secret-two\n"
        "    expires_on: 2026-12-01\n"
    )

    assert (csv_entries[0].name, csv_entries[0].expires_on) == ("ISP A", date(2026, 11, 10))
    assert (clash_entries[0].name, clash_entries[0].protocol, clash_entries[0].expires_on) == (
        "ISP B",
        "socks5",
        date(2026, 12, 1),
    )


@pytest.mark.parametrize(
    "source",
    (
        "host.example:99999:user:secret",
        "https://user:secret@host.example/not-a-proxy",
        "name,protocol,host,port,username,password\nISP,http,host.example,80,user",
    ),
)
def test_parse_isp_import_rejects_invalid_rows_without_echoing_credentials(source: str) -> None:
    with pytest.raises(ValueError) as error:
        parse_isp_import(source)

    assert "secret" not in str(error.value)


@pytest.mark.asyncio
async def test_isp_import_is_saved_without_returning_credentials() -> None:
    class Repository:
        def __init__(self) -> None:
            self.profiles: tuple[ISPProxyProfile, ...] = ()

        async def save_isps(self, profiles: tuple[ISPProxyInput, ...]) -> tuple[ISPProxyProfile, ...]:
            self.profiles = tuple(
                ISPProxyProfile(
                    id=uuid4(),
                    name=item.name,
                    protocol=item.protocol,
                    host=item.host,
                    port=item.port,
                    username=item.username,
                    password=item.password,
                    expires_on=item.expires_on,
                )
                for item in profiles
            )
            return self.profiles

        async def list_isps(self) -> tuple[ISPProxyProfile, ...]:
            return self.profiles

        async def list_isp_bindings(self) -> tuple[tuple[int, UUID], ...]:
            return ()

        async def list_isp_probes(self):
            return ()

    repository: Final = Repository()
    service: Final = ProxyCatalogService(cast(ProxyCatalogRepository, repository))

    profiles: Final = await service.import_isps(
        "name,protocol,host,port,username,password\nISP A,http,proxy.example,3456,alice,top-secret"
    )

    assert len(repository.profiles) == 1
    assert profiles[0].name == "ISP A"
    assert profiles[0].host == "proxy.example"
    assert profiles[0].credentials_configured
    assert "alice" not in repr(profiles)
    assert "top-secret" not in repr(profiles)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "username,password,expected",
    (
        (None, None, ("alice", "secret")),
        ("", "", ("alice", "secret")),
        ("  ", "  ", ("alice", "secret")),
        ("bob", "", ("bob", "secret")),
        ("", "new-secret", ("alice", "new-secret")),
        (None, "  valid-secret  ", ("alice", "  valid-secret  ")),
    ),
)
async def test_isp_edit_preserves_credentials_until_explicitly_cleared(
    username: str | None, password: str | None, expected: tuple[str, str]
) -> None:
    profile_id: Final = uuid4()
    original: Final = ISPProxyProfile(
        id=profile_id,
        name="ISP",
        protocol="http",
        host="proxy.example",
        port=80,
        username="alice",
        password="secret",
    )

    class Repository:
        def __init__(self) -> None:
            self.profile = original

        async def get_isp(self, identity: UUID) -> ISPProxyProfile | None:
            return self.profile if identity == profile_id else None

        async def update_isp(self, profile: ISPProxyProfile) -> ISPProxyProfile:
            self.profile = profile
            return profile

        async def list_isps(self) -> tuple[ISPProxyProfile, ...]:
            return (self.profile,)

        async def list_isp_bindings(self) -> tuple[tuple[int, UUID], ...]:
            return ()

        async def list_isp_probes(self) -> tuple[ISPProbeResult, ...]:
            return ()

    repository: Final = Repository()
    service: Final = ProxyCatalogService(cast(ProxyCatalogRepository, repository))

    preserved: Final = await service.update_isp(
        profile_id,
        name="renamed",
        protocol="http",
        host="proxy.example",
        port=80,
        username=username,
        password=password,
        clear_credentials=False,
        expires_on=None,
        enabled=True,
    )
    assert preserved.credentials_configured
    assert (repository.profile.username, repository.profile.password) == expected

    cleared: Final = await service.update_isp(
        profile_id,
        name="renamed",
        protocol="http",
        host="proxy.example",
        port=80,
        username=None,
        password=None,
        clear_credentials=True,
        expires_on=None,
        enabled=True,
    )
    assert not cleared.credentials_configured
    assert repository.profile.username == ""
    assert repository.profile.password == ""


@pytest.mark.asyncio
async def test_isp_list_includes_persisted_probe_without_exposing_credentials() -> None:
    profile: Final = ISPProxyProfile(
        id=uuid4(), name="ISP", protocol="http", host="proxy.example", port=80,
        username="alice", password="secret",
    )
    probe: Final = ISPProbeResult(
        profile_id=profile.id,
        status="ok",
        checked_at=datetime(2026, 10, 16, tzinfo=timezone.utc),
        exit_ip="8.8.8.8",
        delay_ms=82,
    )

    class Repository:
        async def list_isps(self):
            return (profile,)

        async def list_isp_bindings(self):
            return ()

        async def list_isp_probes(self):
            return (probe,)

    profiles: Final = await ProxyCatalogService(cast(ProxyCatalogRepository, Repository())).list_isps()

    assert profiles[0].credentials_configured
    assert profiles[0].last_probe == probe
    assert "alice" not in repr(profiles)
    assert "secret" not in repr(profiles)


@pytest.mark.asyncio
async def test_manual_isp_view_identifies_its_catalog_source() -> None:
    profile: Final = ISPProxyProfile(
        id=uuid4(), name="manual ISP", protocol="http", host="proxy.example", port=8080,
    )

    class Repository:
        async def list_isps(self):
            return (profile,)

        async def list_isp_bindings(self):
            return ()

        async def list_isp_probes(self):
            return ()

    views: Final = await ProxyCatalogService(cast(ProxyCatalogRepository, Repository())).list_isps()

    assert getattr(views[0], "source", None) == "manual"


@pytest.mark.asyncio
async def test_isp_subscription_configure_and_refresh_reconcile_saved_nodes() -> None:
    from account_pool.proxy_catalog import ISPSubscriptionView

    requested_urls: list[str] = []
    saved: list[tuple[str, tuple[object, ...]]] = []
    reconciled: list[date] = []

    class Repository:
        async def get_isp_subscription_view(self) -> ISPSubscriptionView:
            return ISPSubscriptionView(
                configured=bool(saved), refreshed_at=None, node_count=len(saved[-1][1]) if saved else 0,
                enabled_node_count=len(saved[-1][1]) if saved else 0,
            )

        async def get_isp_subscription_url(self) -> str | None:
            return None if not saved else saved[-1][0]

        async def save_isp_subscription(self, url: str, nodes: tuple[object, ...]) -> ISPSubscriptionView:
            saved.append((url, nodes))
            return ISPSubscriptionView(
                configured=True, refreshed_at=datetime.now(timezone.utc), node_count=len(nodes),
                enabled_node_count=len(nodes),
            )

    async def fetch(url: str) -> str:
        requested_urls.append(url)
        return "proxies:\n  - name: home\n    type: ss\n    server: edge.example\n    port: 443\n    cipher: aes-128-gcm\n"

    async def reconcile(today: date) -> None:
        reconciled.append(today)

    service: Final = ProxyCatalogService(
        cast(ProxyCatalogRepository, Repository()), reconcile, fetch_subscription=fetch,
    )

    configured: Final = await service.configure_isp_subscription(" https://isp.example/list.yaml ")
    refreshed: Final = await service.refresh_isp_subscription()

    assert configured.configured and configured.node_count == 1
    assert refreshed.configured and refreshed.node_count == 1
    assert requested_urls == ["https://isp.example/list.yaml", "https://isp.example/list.yaml"]
    assert len(saved) == 2 and saved[0][1][0].name == "home"
    assert len(reconciled) == 2


@pytest.mark.asyncio
async def test_manager_isp_subscription_routes_configure_refresh_and_redact_failures() -> None:
    import httpx
    from account_pool.api import create_router
    from account_pool.proxy_catalog import ISPSubscriptionView
    from account_pool.proxy_catalog_service import ProxyCatalogService
    from fastapi import FastAPI

    saved: list[tuple[str, tuple[object, ...]]] = []
    requested: list[str] = []

    class Repository:
        async def get_isp_subscription_view(self) -> ISPSubscriptionView:
            return ISPSubscriptionView(
                configured=bool(saved),
                refreshed_at=None,
                node_count=0 if not saved else len(saved[-1][1]),
                enabled_node_count=0 if not saved else len(saved[-1][1]),
            )

        async def get_isp_subscription_url(self) -> str | None:
            return None if not saved else saved[-1][0]

        async def save_isp_subscription(
            self, url: str, nodes: tuple[object, ...]
        ) -> ISPSubscriptionView:
            saved.append((url, nodes))
            return ISPSubscriptionView(
                configured=True, refreshed_at=datetime.now(timezone.utc), node_count=len(nodes),
                enabled_node_count=len(nodes),
            )

    async def fetch(url: str) -> str:
        requested.append(url)
        if "private" in url:
            raise ValueError("private subscription token must not be returned")
        return "proxies:\n  - name: home\n    type: ss\n    server: edge.example\n    port: 443\n    cipher: aes-128-gcm\n"

    app = FastAPI()
    app.include_router(
        create_router(
            object(),
            "manager-test",
            proxy_catalog=ProxyCatalogService(cast(ProxyCatalogRepository, Repository()), fetch_subscription=fetch),
        )
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        unauthorized = await client.put(
            "/api/proxy-isps/subscription", json={"url": "https://isp.example/sub.yaml"}
        )
        headers = {"Authorization": "Bearer manager-test"}
        status = await client.get("/api/proxy-isps/subscription", headers=headers)
        configured = await client.put(
            "/api/proxy-isps/subscription",
            json={"url": "https://isp.example/sub.yaml"},
            headers=headers,
        )
        refreshed = await client.post("/api/proxy-isps/subscription/refresh", headers=headers)
        failed = await client.put(
            "/api/proxy-isps/subscription",
            json={"url": "https://private.example/sub.yaml"},
            headers=headers,
        )

    assert unauthorized.status_code == 401
    assert status.status_code == 200 and status.json()["configured"] is False
    assert configured.status_code == 200 and configured.json()["node_count"] == 1
    assert refreshed.status_code == 200 and refreshed.json()["node_count"] == 1
    assert failed.status_code == 422
    assert "private subscription token" not in failed.text
    assert requested == [
        "https://isp.example/sub.yaml",
        "https://isp.example/sub.yaml",
        "https://private.example/sub.yaml",
    ]


@pytest.mark.asyncio
async def test_expiry_check_deactivates_profiles_then_enforces_gateway_state() -> None:
    profile_id: Final = uuid4()

    class Catalog:
        async def deactivate_expired(self, today: date) -> tuple[UUID, ...]:
            return (profile_id,)

    class Gateways:
        def __init__(self) -> None:
            self.checked: list[date] = []

        async def enforce_inactive_isps(self, today: date) -> None:
            self.checked.append(today)

    gateways: Final = Gateways()
    scheduler: Final = ISPExpiryScheduler(
        cast(ExpiringProxyCatalog, Catalog()), cast(ISPBoundGateways, gateways)
    )

    deactivated: Final = await scheduler.run_once(date(2026, 10, 14))

    assert deactivated == (profile_id,)
    assert gateways.checked == [date(2026, 10, 14)]
