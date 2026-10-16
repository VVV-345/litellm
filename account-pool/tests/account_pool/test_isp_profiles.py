"""测试 ISP 代理导入格式和凭据展示边界。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Final, cast
from uuid import UUID, uuid4

import pytest

from account_pool.isp_probe import ISPProbeResult
from account_pool.proxy_catalog import ISPProxyInput, ISPProxyProfile, parse_isp_import
from account_pool.proxy_catalog_repository import ProxyCatalogRepository
from account_pool.proxy_catalog_service import ProxyCatalogService
from account_pool.proxy_expiry import ExpiringProxyCatalog, ISPBoundGateways, ISPExpiryScheduler


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
