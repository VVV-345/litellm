"""验证 ISP 可直接作为代理出口，凭据不进入公开目录且失效后拒绝使用。"""

from datetime import date, timedelta
from pathlib import Path
from typing import Final, cast
from uuid import UUID

import pytest
from account_pool.config import validate_proxy_profile_url
from account_pool.direct_isp_profiles import DirectISPProxyProfiles
from account_pool.domain import (
    CreateEnvironmentRequest,
    EnvironmentStatus,
    ProxyMode,
    ProxyProfile,
    UpdateEnvironmentRequest,
)
from account_pool.ports import ProxyProfileRepository
from account_pool.proxy_catalog import ISPProxyCatalogProfile, ISPProxyProfile, ISPSubscriptionNodeProfile
from account_pool.proxy_catalog_repository import ProxyCatalogRepository
from account_pool.result import Failure
from account_pool.secrets import EnvironmentSecretDeriver
from account_pool.service import EnvironmentService
from account_pool.shared.error_safety import safe_error
from test_account_pool import FakeCLIProxy, FakeRuntime, FieldsCLI, MemoryRepository, _fake_channels, _record, _settings

ISP_ID: Final = UUID("d33cb1de-9247-432a-a827-7503ab1c9f95")
PROFILE_ID: Final = f"isp-direct-{ISP_ID}"


class Catalog:
    def __init__(self, *profiles: ISPProxyCatalogProfile) -> None:
        self.profiles = profiles

    async def list_isps(self) -> tuple[ISPProxyCatalogProfile, ...]:
        return self.profiles

    async def get_isp(self, profile_id: UUID) -> ISPProxyCatalogProfile | None:
        return next((profile for profile in self.profiles if profile.id == profile_id), None)


class Profiles:
    async def list(self) -> tuple[ProxyProfile, ...]:
        return (ProxyProfile(id="gateway", name="Existing gateway", protocol="http"),)

    async def get_url(self, profile_id: str) -> str | None:
        return "http://gateway:7890" if profile_id == "gateway" else None


def direct_profiles(catalog: Catalog) -> ProxyProfileRepository:
    return DirectISPProxyProfiles(cast(ProxyProfileRepository, Profiles()), cast(ProxyCatalogRepository, catalog))


def isp(**updates: object) -> ISPProxyProfile:
    return ISPProxyProfile.model_validate({
        "id": ISP_ID, "name": "Residential", "protocol": "socks5", "host": "isp.example", "port": 1080,
        "username": "user@name", "password": "p:/@#%", **updates,
    })


@pytest.mark.asyncio
async def test_directory_and_private_resolution_do_not_require_a_gateway() -> None:
    profiles: Final = direct_profiles(Catalog(isp()))
    public: Final = await profiles.list()
    assert tuple(profile.id for profile in public) == ("gateway", PROFILE_ID)
    assert public[1].protocol == "socks5"
    assert "password" not in public[1].model_dump_json()
    assert "user@name" not in public[1].model_dump_json()
    assert await profiles.get_url(PROFILE_ID) == "socks5://user%40name:p%3A%2F%40%23%25@isp.example:1080"
    assert await profiles.get_url("gateway") == "http://gateway:7890"


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", [isp(enabled=False), isp(expires_on=date.today()), isp(host="evil/path")])
async def test_unusable_isp_is_not_selectable_or_resolvable(profile: ISPProxyProfile) -> None:
    profiles: Final = direct_profiles(Catalog(profile))
    assert tuple(item.id for item in await profiles.list()) == ("gateway",)
    assert await profiles.get_url(PROFILE_ID) is None


@pytest.mark.asyncio
async def test_selection_rechecks_live_catalog_and_preserves_ipv6_and_credential_encoding() -> None:
    catalog: Final = Catalog(isp(host="2001:db8::1", expires_on=date.today() + timedelta(days=1)))
    profiles: Final = direct_profiles(catalog)
    assert await profiles.get_url(PROFILE_ID) == "socks5://user%40name:p%3A%2F%40%23%25@[2001:db8::1]:1080"
    catalog.profiles = ()
    assert await profiles.get_url(PROFILE_ID) is None
    assert await profiles.get_url("isp-direct-invalid") is None


@pytest.mark.asyncio
async def test_complex_subscription_requires_gateway() -> None:
    profile: Final = ISPSubscriptionNodeProfile(
        id=ISP_ID, name="TLS socks", proxy_type="socks5",
        config={"type": "socks5", "server": "isp.example", "port": 1080, "tls": True},
    )
    profiles: Final = direct_profiles(Catalog(profile))
    assert await profiles.get_url(PROFILE_ID) is None
    assert len(await profiles.list()) == 1


@pytest.mark.asyncio
async def test_simple_http_subscription_can_be_used_directly() -> None:
    profile: Final = ISPSubscriptionNodeProfile(
        id=ISP_ID, name="HTTPS ISP", proxy_type="http",
        config={"type": "http", "server": "isp.example", "port": 8443, "tls": True, "password": "p@ss"},
    )
    assert await direct_profiles(Catalog(profile)).get_url(PROFILE_ID) == "https://:p%40ss@isp.example:8443"


@pytest.mark.asyncio
async def test_oauth_receives_direct_isp_before_authorization_without_exposing_credentials(tmp_path: Path) -> None:
    cli: Final = FakeCLIProxy()
    runtime: Final = FakeRuntime()
    service: Final = EnvironmentService(
        settings=_settings(tmp_path), repository=MemoryRepository(_record(status=EnvironmentStatus.READY)),
        runtime=runtime, cli_proxy=cli, proxy_profiles=direct_profiles(Catalog(isp())),
        secrets=EnvironmentSecretDeriver("s" * 32), channels=_fake_channels(runtime, cli),
    )
    result: Final = await service.create_environment(CreateEnvironmentRequest(name="ISP OAuth", proxy_profile_id=PROFILE_ID))
    assert not isinstance(result, Failure)
    assert cli.events[:2] == ["proxy:socks5://user%40name:p%3A%2F%40%23%25@isp.example:1080", "oauth"]
    assert result.value.environment.proxy_profile_id == PROFILE_ID
    assert "user%40name" not in result.value.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining", [(), (isp(enabled=False),), (isp(password="rotated"),)])
async def test_changed_or_removed_isp_blocks_existing_card_and_reauthorization(
    tmp_path: Path, remaining: tuple[ISPProxyCatalogProfile, ...],
) -> None:
    cli: Final = FakeCLIProxy()
    runtime: Final = FakeRuntime()
    catalog: Final = Catalog(isp())
    repository: Final = MemoryRepository(_record(status=EnvironmentStatus.READY))
    service: Final = EnvironmentService(
        settings=_settings(tmp_path), repository=repository, runtime=runtime, cli_proxy=cli,
        proxy_profiles=direct_profiles(catalog), secrets=EnvironmentSecretDeriver("s" * 32),
        channels=_fake_channels(runtime, cli),
    )
    created: Final = await service.create_environment(CreateEnvironmentRequest(name="ISP", proxy_profile_id=PROFILE_ID))
    assert not isinstance(created, Failure)
    record: Final = await repository.get(created.value.environment.id)
    assert record is not None
    assert await service.proxy_available(record)
    catalog.profiles = remaining
    assert not await service.proxy_available(record)
    attempts: Final = cli.events.count("oauth")
    assert isinstance(await service.authorize_environment(record.id), Failure)
    assert cli.events.count("oauth") == attempts


def test_authenticated_socks_url_is_allowed_only_for_a_direct_isp_profile() -> None:
    url: Final = "socks5://user:password@isp.example:1080"
    assert validate_proxy_profile_url(url, profile_id=PROFILE_ID) == url
    with pytest.raises(ValueError):
        validate_proxy_profile_url(url)


def test_socks_proxy_credentials_are_redacted_from_errors() -> None:
    assert safe_error(ValueError("connect socks5://alice:private-secret@isp.example:1080 failed")) == (
        "connect socks5://isp.example:1080 failed"
    )


@pytest.mark.asyncio
async def test_isp_cannot_be_hidden_in_an_untracked_auth_file_override(tmp_path: Path) -> None:
    cli: Final = FieldsCLI()
    runtime: Final = FakeRuntime()
    record: Final = _record(status=EnvironmentStatus.READY)
    service: Final = EnvironmentService(
        settings=_settings(tmp_path), repository=MemoryRepository(record), runtime=runtime, cli_proxy=cli,
        proxy_profiles=direct_profiles(Catalog(isp())), secrets=EnvironmentSecretDeriver("s" * 32),
        channels=_fake_channels(runtime, cli),
    )
    assert isinstance(await service.patch_auth_file_fields(record.id, {"proxy_profile_id": PROFILE_ID}), Failure)
    assert not cli.fields_calls


@pytest.mark.asyncio
async def test_saving_card_proxy_applies_current_isp_credentials(tmp_path: Path) -> None:
    cli: Final = FakeCLIProxy()
    runtime: Final = FakeRuntime()
    record: Final = _record(status=EnvironmentStatus.READY)
    repository: Final = MemoryRepository(record)
    service: Final = EnvironmentService(
        settings=_settings(tmp_path), repository=repository, runtime=runtime, cli_proxy=cli,
        proxy_profiles=direct_profiles(Catalog(isp(password="rotated"))), secrets=EnvironmentSecretDeriver("s" * 32),
        channels=_fake_channels(runtime, cli),
    )
    result: Final = await service.update_environment(record.id, UpdateEnvironmentRequest(
        version=record.version, name=record.name, concurrency_limit=record.concurrency_limit,
        enabled=True, manual_cooldown=False, proxy_mode=ProxyMode.PROFILE,
        proxy_profile_id=PROFILE_ID, enabled_models=record.enabled_models,
    ))
    assert not isinstance(result, Failure)
    saved: Final = await repository.get(record.id)
    assert saved is not None and await service.proxy_available(saved)
    assert "proxy:socks5://user%40name:rotated@isp.example:1080" in cli.events
