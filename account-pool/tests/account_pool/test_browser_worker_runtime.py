"""验证受控浏览器 Compose 网络、镜像和会话凭据隔离。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final
from uuid import uuid4

import pytest
import yaml
from account_pool.compose_renderer import render_oauth_browser_compose
from account_pool.compose_runtime import ComposeRuntime, DockerProcess
from account_pool.config import Settings
from account_pool.oauth_browser import OAuthBrowserSession, OAuthBrowserSessionStatus
from account_pool.shared.secrets import EnvironmentSecretDeriver


def _settings(tmp_path: Path, browser_image: str) -> Settings:
    return Settings(
        database_url="postgresql://unused",
        data_root=tmp_path,
        manager_token="m" * 32,
        secret_seed="s" * 32,
        ssh_host="example.com",
        ssh_user="operator",
        oauth_browser_image=browser_image,
        control_network="litellm-control",
    )


def _session() -> OAuthBrowserSession:
    now: Final = datetime(2026, 10, 7, tzinfo=timezone.utc)
    return OAuthBrowserSession(
        id=uuid4(),
        environment_id=uuid4(),
        status=OAuthBrowserSessionStatus.STARTING,
        proxy_profile_id="proxy-1",
        proxy_url_fingerprint="a" * 64,
        ticket_digest="b" * 64,
        callback_token_digest="c" * 64,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )


def test_browser_compose_keeps_worker_internal_and_binds_only_selected_proxy(tmp_path: Path) -> None:
    session: Final = _session()
    settings: Final = _settings(tmp_path, f"registry.example/browser@sha256:{'d' * 64}")

    rendered: Final = yaml.safe_load(
        render_oauth_browser_compose(
            session,
            settings,
            proxy_url="https://proxy.example:8443",
            authorization_url="https://auth.example/authorize?state=oauth-state",
            callback_token="callback-ticket-secret",
            callback_port=1455,
            callback_path="/auth/callback",
        )
    )
    services: Final = rendered["services"]
    browser: Final = services["browser"]
    callback: Final = services["callback-relay"]
    relay: Final = services["egress-relay"]

    assert "ports" not in browser
    assert browser["read_only"] is True
    assert "volumes" not in browser
    assert rendered["networks"]["browser"]["internal"] is True
    assert browser["networks"] == {"browser": {"aliases": ["browser"]}}
    assert browser["environment"]["CHROME_PROXY"] == "http://egress-relay:8080"
    assert browser["environment"]["OAUTH_AUTHORIZATION_URL"] == "https://auth.example/authorize?state=oauth-state"
    assert "CALLBACK_TOKEN" not in browser["environment"]
    assert browser["networks"] == {"browser": {"aliases": ["browser"]}}
    assert callback["command"] == ["callback-relay"]
    assert callback["networks"] == ["browser", "control"]
    assert callback["environment"]["CALLBACK_TOKEN"] == "callback-ticket-secret"
    assert callback["environment"]["MANAGER_CALLBACK_URL"].startswith(
        f"http://{settings.manager_container}:8091/internal/oauth-browser-sessions/"
    )
    assert relay["command"] == ["egress-relay"]
    assert relay["environment"]["PROXY_URL"] == "https://proxy.example:8443"
    assert relay["networks"] == ["browser", "egress"]
    assert "ports" not in relay
    assert relay["read_only"] is True
    assert "volumes" not in relay
    assert rendered["networks"]["egress"]["internal"] is False
    assert rendered["networks"]["control"] == {"external": True, "name": "litellm-control"}
    assert all(
        "control" not in service.get("networks", {})
        if isinstance(service.get("networks"), dict)
        else "control" not in service.get("networks", [])
        for service in (browser, relay)
    )
    assert all("docker.sock" not in str(service).lower() for service in services.values())
    assert rendered["name"] == f"account-pool-oauth-browser-{session.id.hex}"


def test_browser_compose_rejects_proxy_credentials_and_direct_browser_egress(tmp_path: Path) -> None:
    settings: Final = _settings(tmp_path, f"registry.example/browser@sha256:{'d' * 64}")

    with pytest.raises(ValueError, match="credential-free"):
        render_oauth_browser_compose(
            _session(),
            settings,
            proxy_url="https://user:password@proxy.example:8443",
            authorization_url="https://auth.example/authorize?state=oauth-state",
            callback_token="callback-ticket-secret",
            callback_port=1455,
            callback_path="/auth/callback",
        )


def test_browser_compose_rejects_mutable_worker_image_references(tmp_path: Path) -> None:
    settings: Final = _settings(tmp_path, "registry.example/browser:latest")

    with pytest.raises(ValueError, match="digest"):
        render_oauth_browser_compose(
            _session(),
            settings,
            proxy_url="http://proxy.example:8080",
            authorization_url="https://auth.example/authorize?state=oauth-state",
            callback_token="callback-ticket-secret",
            callback_port=1455,
            callback_path="/auth/callback",
        )


class _CompletedProcess:
    returncode: int | None = 0
    stdin = None

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b""

    def kill(self) -> None:
        return None


@pytest.mark.asyncio
async def test_browser_runtime_removes_session_compose_and_private_files(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    async def runner(arguments: tuple[str, ...], environment: dict[str, str]) -> DockerProcess:
        calls.append(arguments)
        return _CompletedProcess()

    settings: Final = _settings(tmp_path, f"registry.example/browser@sha256:{'d' * 64}")
    runtime: Final = ComposeRuntime(settings, EnvironmentSecretDeriver(settings.secret_seed), runner=runner)
    session: Final = _session()

    await runtime.start_oauth_browser(
        session,
        proxy_url="https://proxy.example:8443",
        authorization_url="https://auth.example/authorize?state=oauth-state",
        callback_token="callback-ticket-secret",
        callback_port=1455,
        callback_path="/auth/callback",
    )
    directory: Final = runtime.oauth_browser_dir(session.id)
    assert (directory / "compose.yaml").exists()
    assert calls[-1][0:7] == (
        "docker",
        "compose",
        "--project-name",
        f"account-pool-oauth-browser-{session.id.hex}",
        "--file",
        str(directory / "compose.yaml"),
        "up",
    )

    await runtime.remove_oauth_browser(session.id)
    assert not directory.exists()
    assert calls[-1][-2:] == ("--volumes", "--remove-orphans")
