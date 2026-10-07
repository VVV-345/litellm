"""验证受控浏览器 Compose 网络、镜像和会话凭据隔离。"""

from __future__ import annotations

import http.server
import importlib.util
import socket
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Final
from urllib.request import Request
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
    assert browser["environment"]["DISPLAY"] == ":99"
    assert browser["environment"]["OAUTH_AUTHORIZATION_URL"] == "https://auth.example/authorize?state=oauth-state"
    assert "CALLBACK_TOKEN" not in browser["environment"]
    assert browser["networks"] == {"browser": {"aliases": ["browser"]}}
    assert callback["command"] == ["callback-relay"]
    assert callback["networks"] == {
        "browser": None,
        "control": {"aliases": [f"{rendered['name']}-callback-relay"]},
    }
    assert callback["environment"]["CALLBACK_TOKEN"] == "callback-ticket-secret"
    assert callback["environment"]["MANAGER_CALLBACK_URL"].startswith(
        f"http://{settings.manager_container}:8091/internal/oauth-browser-sessions/"
    )
    assert relay["command"] == ["egress-relay"]
    assert relay["environment"]["PROXY_URL"] == "https://proxy.example:8443"
    assert relay["networks"] == ["browser", "egress"]
    assert relay["extra_hosts"] == ["host.docker.internal:host-gateway"]
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
    assert browser["depends_on"] == {
        "callback-relay": {"condition": "service_healthy"},
        "egress-relay": {"condition": "service_healthy"},
    }
    for name in ("browser", "callback-relay", "egress-relay"):
        assert services[name]["healthcheck"]["test"] == ["CMD", "python", "/app/entrypoint.py", "healthcheck", name]


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
    assert "--wait" in calls[-1]
    assert calls[-1][calls[-1].index("--wait-timeout") + 1] == "45"
    assert calls[-1][calls[-1].index("--pull") + 1] == "never"

    await runtime.remove_oauth_browser(session.id)
    assert not directory.exists()
    assert calls[-1][-2:] == ("--volumes", "--remove-orphans")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("browser is unhealthy", "worker image is missing"))
async def test_browser_runtime_removes_unavailable_session(tmp_path: Path, failure: str) -> None:
    calls: list[tuple[str, ...]] = []

    class UnhealthyProcess(_CompletedProcess):
        returncode = 1

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", failure.encode("utf-8")

    async def runner(arguments: tuple[str, ...], environment: dict[str, str]) -> DockerProcess:
        calls.append(arguments)
        return UnhealthyProcess() if "up" in arguments and "--wait" in arguments else _CompletedProcess()

    settings: Final = _settings(tmp_path, f"registry.example/browser@sha256:{'d' * 64}")
    runtime: Final = ComposeRuntime(settings, EnvironmentSecretDeriver(settings.secret_seed), runner=runner)
    session: Final = _session()

    with pytest.raises(RuntimeError, match=failure):
        await runtime.start_oauth_browser(
            session,
            proxy_url="http://proxy.example:8080",
            authorization_url="https://auth.example/authorize",
            callback_token="callback-ticket-secret",
            callback_port=1455,
            callback_path="/auth/callback",
        )

    assert calls[-1][-3:] == ("down", "--volumes", "--remove-orphans")
    assert not runtime.oauth_browser_dir(session.id).exists()


@pytest.fixture
def worker() -> ModuleType:
    path: Final = Path(__file__).resolve().parents[2] / "browser-worker" / "entrypoint.py"
    spec: Final = importlib.util.spec_from_file_location("oauth_browser_worker", path)
    assert spec is not None and spec.loader is not None
    module: Final = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("navigation_fails", (False, True))
def test_browser_uses_loopback_bypass_and_becomes_ready_only_after_navigation(
    worker: ModuleType, tmp_path: Path, navigation_fails: bool
) -> None:
    ready: Final = tmp_path / "browser-ready"
    launches: list[dict[str, object]] = []
    closed: list[bool] = []

    class Finished(BaseException):
        pass

    class Page:
        def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
            assert not ready.exists()
            assert (url, wait_until, timeout) == ("https://auth.example/authorize", "domcontentloaded", 30_000)
            if navigation_fails:
                raise RuntimeError("navigation failed with private details")

        def wait_for_timeout(self, timeout: int) -> None:
            assert ready.exists()
            raise Finished()

    class Browser:
        def new_page(self) -> Page:
            return Page()

        def close(self) -> None:
            closed.append(True)

    class Chromium:
        def launch(self, *, headless: bool, proxy: dict[str, str]) -> Browser:
            launches.append({"headless": headless, "proxy": proxy})
            return Browser()

    with pytest.raises(RuntimeError if navigation_fails else Finished) as error:
        worker._run_browser_page(Chromium(), "https://auth.example/authorize", "http://egress-relay:8080", ready)

    assert launches == [
        {
            "headless": False,
            "proxy": {"server": "http://egress-relay:8080", "bypass": "localhost,127.0.0.1,[::1]"},
        }
    ]
    assert closed == [True]
    assert not ready.exists()
    assert "private details" not in str(error.value)


def test_browser_health_requires_page_and_listeners(worker: ModuleType, tmp_path: Path) -> None:
    ready: Final = tmp_path / "browser-ready"
    ports: list[int] = []

    with pytest.raises(RuntimeError, match="not ready"):
        worker._check_health("browser", ready_file=ready, check_port=ports.append, callback_port=1455)
    assert ports == []

    ready.touch()
    worker._check_health("browser", ready_file=ready, check_port=ports.append, callback_port=1455)
    assert ports == [5900, 1455]


@pytest.mark.parametrize("mode, ports", (("callback-relay", [8092, 8093]), ("egress-relay", [8080])))
def test_relay_health_checks_required_listeners(worker: ModuleType, mode: str, ports: list[int]) -> None:
    checked: list[int] = []
    worker._check_health(mode, check_port=checked.append)
    assert checked == ports


def test_health_probe_fails_when_listener_is_missing(worker: ModuleType) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port: Final = listener.getsockname()[1]
        worker._check_port(port)

    with pytest.raises(OSError):
        worker._check_port(port)


def test_worker_health_command_reports_unavailable_service_without_traceback(worker: ModuleType) -> None:
    result: Final = subprocess.run(
        (sys.executable, str(worker.__file__), "healthcheck", "invalid-service"),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.strip() == "OAuth browser service is not ready"


@pytest.mark.parametrize("status", (204, 409))
def test_callback_relay_preserves_http_status_without_response_body(worker: ModuleType, status: int) -> None:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return None

    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread: Final = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request: Final = Request(f"http://127.0.0.1:{server.server_port}/callback", data=b"{}", method="POST")
            assert worker._callback_response_status(request) == status
        finally:
            server.shutdown()
            thread.join(timeout=2)
