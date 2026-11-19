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
from uuid import UUID, uuid4

import pytest
import yaml
from account_pool.compose_renderer import render_oauth_browser_compose
from account_pool.compose_runtime import ComposeRuntime, DockerProcess
from account_pool.config import Settings
from account_pool.manager_logs import ManagerDiagnostic
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
    assert callback["mem_limit"] == "256m"
    assert callback["user"] == "10001:10001"
    assert callback["cap_drop"] == ["ALL"]
    assert "ports" not in callback
    assert callback["networks"] == {
        "browser": None,
        "control": {"aliases": [f"oauth-control-{session.id.hex}"]},
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
    assert browser["environment"]["OAUTH_BROWSER_NAVIGATION_TIMEOUT_SECONDS"] == "60"
    assert browser["healthcheck"]["start_period"] == "120s"
    assert browser["healthcheck"]["retries"] == 3


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
    assert calls[-1][calls[-1].index("--wait-timeout") + 1] == "120"
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


@pytest.mark.asyncio
async def test_browser_startup_command_budget_does_not_change_other_docker_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import account_pool.compose_runtime as runtime_module

    timeouts: Final[list[float]] = []
    communicate: Final = runtime_module.communicate_with_timeout

    async def observed(process: DockerProcess, timeout_seconds: float) -> tuple[bytes, bytes]:
        timeouts.append(timeout_seconds)
        return await communicate(process, timeout_seconds)

    async def runner(arguments: tuple[str, ...], environment: dict[str, str]) -> DockerProcess:
        return _CompletedProcess()

    monkeypatch.setattr(runtime_module, "communicate_with_timeout", observed)
    settings: Final = _settings(tmp_path, f"registry.example/browser@sha256:{'d' * 64}").model_copy(
        update={"docker_command_timeout_seconds": 7.0}
    )
    runtime: Final = ComposeRuntime(settings, EnvironmentSecretDeriver(settings.secret_seed), runner=runner)
    session: Final = _session()
    await runtime.start_oauth_browser(
        session,
        proxy_url="http://proxy.example:8080",
        authorization_url="https://auth.example/authorize",
        callback_token="test-secret",
        callback_port=1455,
        callback_path="/auth/callback",
    )
    await runtime.remove_oauth_browser(session.id)
    assert timeouts == [150.0, 7.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("diagnostics_result", ["ok", "exit", "timeout"])
async def test_browser_startup_diagnostics_are_safe_bounded_and_precede_cleanup(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    diagnostics_result: str,
) -> None:
    import asyncio
    import json

    import account_pool.compose_runtime as runtime_module

    calls: Final[list[tuple[str, ...]]] = []
    timeouts: Final[list[float]] = []
    communicate: Final = runtime_module.communicate_with_timeout

    async def observed(process: DockerProcess, timeout_seconds: float) -> tuple[bytes, bytes]:
        timeouts.append(timeout_seconds)
        return await communicate(process, 0.01 if timeout_seconds == 5 else timeout_seconds)

    monkeypatch.setattr(runtime_module, "communicate_with_timeout", observed)

    class Process(_CompletedProcess):
        def __init__(self, code: int, output: bytes = b"", error_output: bytes = b"") -> None:
            self.returncode = code
            self.output = output
            self.error_output = error_output

        async def communicate(self) -> tuple[bytes, bytes]:
            return self.output, self.error_output

    class PendingDiagnostic(Process):
        def __init__(self) -> None:
            super().__init__(0)
            self.killed = asyncio.Event()

        async def communicate(self) -> tuple[bytes, bytes]:
            await self.killed.wait()
            return b"", b""

        def kill(self) -> None:
            self.returncode = -9
            self.killed.set()

    async def runner(arguments: tuple[str, ...], environment: dict[str, str]) -> DockerProcess:
        calls.append(arguments)
        if "up" in arguments:
            return Process(1, b"browser is unhealthy")
        if diagnostics_result == "timeout" and ("ps" in arguments or "logs" in arguments):
            return PendingDiagnostic()
        if "ps" in arguments:
            return Process(
                1 if diagnostics_result == "exit" else 0,
                json.dumps(
                    {
                        "Service": "browser",
                        "State": "exited",
                        "Health": "unhealthy",
                        "ExitCode": 1,
                        "Command": "DO_NOT_LOG",
                        "Environment": {"PASSWORD": "DO_NOT_LOG"},
                    }
                ).encode(),
            )
        if "logs" in arguments:
            return Process(
                1 if diagnostics_result == "exit" else 0,
                b"raw password DO_NOT_LOG\n",
                b'oauth_browser_startup {"stage":"page_navigation","status":"failed","elapsed_ms":60001,'
                b'"reason":"timeout","secret":"DO_NOT_LOG"}\n'
                b'oauth_browser_startup {"stage":"DO_NOT_LOG","status":"failed","elapsed_ms":1}\n',
            )
        return Process(0)

    settings: Final = _settings(tmp_path, f"registry.example/browser@sha256:{'d' * 64}")
    session: Final = _session()
    recorded: Final[list[ManagerDiagnostic]] = []

    async def record(card_id: UUID, session_id: UUID | None, diagnostic: ManagerDiagnostic) -> None:
        assert card_id == session.environment_id and session_id == session.id
        assert all("down" not in call for call in calls)
        recorded.append(diagnostic)

    runtime: Final = ComposeRuntime(
        settings, EnvironmentSecretDeriver(settings.secret_seed), runner=runner, record_diagnostic=record,
    )
    with pytest.raises(RuntimeError, match="unhealthy"):
        await runtime.start_oauth_browser(
            session,
            proxy_url="http://proxy.example:8080",
            authorization_url="https://auth.example/?state=DO_NOT_LOG",
            callback_token="DO_NOT_LOG",
            callback_port=1455,
            callback_path="/auth/callback",
        )
    assert [next(word for word in ("up", "ps", "logs", "down") if word in call) for call in calls] == [
        "up",
        "ps",
        "logs",
        "down",
    ]
    assert "--tail" in calls[2] and calls[2][calls[2].index("--tail") + 1] == "30"
    assert timeouts == [150.0, 5, 5, settings.docker_command_timeout_seconds]
    assert "DO_NOT_LOG" not in caplog.text
    assert str(session.id) in caplog.text
    assert "compose_failed" in caplog.text
    assert recorded[:2] == [
        ManagerDiagnostic(step="compose", state="started"),
        ManagerDiagnostic(step="compose", state="failed", reason="compose_failed"),
    ]
    if diagnostics_result == "ok":
        assert "page_navigation" in caplog.text and "60001" in caplog.text
        assert "unhealthy" in caplog.text
        assert recorded[2:] == [
            ManagerDiagnostic(step="page_navigation", state="failed", reason="timeout", duration_ms=60_001),
        ]
    else:
        assert len(recorded) == 2
    assert not runtime.oauth_browser_dir(session.id).exists()


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
            assert (url, wait_until, timeout) == ("https://auth.example/authorize", "domcontentloaded", 60_000)
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


@pytest.mark.asyncio
async def test_browser_startup_cancellation_kills_command_and_cleans_session(tmp_path: Path) -> None:
    import asyncio

    entered: Final = asyncio.Event()
    killed: Final = asyncio.Event()
    calls: Final[list[tuple[str, ...]]] = []

    class PendingProcess(_CompletedProcess):
        returncode = None

        async def communicate(self) -> tuple[bytes, bytes]:
            entered.set()
            await killed.wait()
            return b"", b""

        def kill(self) -> None:
            self.returncode = -9
            killed.set()

    async def runner(arguments: tuple[str, ...], environment: dict[str, str]) -> DockerProcess:
        calls.append(arguments)
        return PendingProcess() if "up" in arguments else _CompletedProcess()

    settings: Final = _settings(tmp_path, f"registry.example/browser@sha256:{'d' * 64}")
    runtime: Final = ComposeRuntime(settings, EnvironmentSecretDeriver(settings.secret_seed), runner=runner)
    session: Final = _session()
    task: Final = asyncio.create_task(
        runtime.start_oauth_browser(
            session,
            proxy_url="http://proxy.example:8080",
            authorization_url="https://auth.example/authorize",
            callback_token="test-secret",
            callback_port=1455,
            callback_path="/auth/callback",
        )
    )
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert killed.is_set()
    assert calls[-1][-3:] == ("down", "--volumes", "--remove-orphans")
    assert not runtime.oauth_browser_dir(session.id).exists()


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
def test_callback_relay_preserves_http_status_without_response_body(
    worker: ModuleType, status: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
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
