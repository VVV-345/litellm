"""托管安装 ID 只在补丁镜像上生效，旧卡片要换成这个镜像后才重建。"""

from __future__ import annotations

import importlib.util
import socket
import struct
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import ModuleType
from typing import Final
from uuid import uuid4

import pytest
import yaml
from account_pool.compose_renderer import render_compose
from account_pool.compose_runtime import ComposeRuntime
from account_pool.config import DEFAULT_CLI_PROXY_IMAGE, Settings
from account_pool.domain import EnvironmentRecord, EnvironmentStatus, Provider, ProxyMode, QuotaSnapshot, utc_now
from account_pool.shared.secrets import EnvironmentSecretDeriver

_ADAPTER: Final = Path(__file__).resolve().parents[2] / "cliproxy-adapter"
_REPO: Final = Path(__file__).resolve().parents[3]
_OLD_IMAGE: Final = "ghcr.io/vvv-345/cliproxyapi:sha-bebf587f5a940af676f6e503065140d4991ae1f3"
_NEW_IMAGE: Final = "ghcr.io/vvv-345/litellm-cliproxyapi@sha256:" + "a" * 64


def _load(name: str) -> ModuleType:
    spec: Final = importlib.util.spec_from_file_location(name, _ADAPTER / f"{name}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"missing adapter module {name}")
    module: Final = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_IMAGE_REF: Final = _load("image_ref")
_PROBE: Final = _load("probe_managed_identity")


@contextmanager
def _capability_server(
    *, resets: int = 0, status: int = 200, unauthorized_status: int = 401,
    body: bytes = b'{"supported":true,"version":1}',
) -> Iterator[tuple[int, list[str | None]]]:
    attempts: list[str | None] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            key = self.headers.get("X-Management-Key")
            attempts.append(key)
            if len(attempts) <= resets:
                self.connection.setsockopt(
                    socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("hh" if sys.platform == "win32" else "ii", 1, 0)
                )
                self.close_connection = True
                return
            self.send_response(unauthorized_status if key is None else status)
            self.end_headers()
            self.wfile.write(b"unauthorized" if key is None else body)

        def log_message(self, *_arguments: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, attempts
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_probe_waits_for_http_after_tcp_accept() -> None:
    with _capability_server(resets=2) as (port, attempts):
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
        _PROBE.assert_capability(port)
        assert attempts == [None, None, None, "probe-managed-identity-key"]


@pytest.mark.parametrize(
    "status, body",
    [(404, b"not found"), (200, b'{"supported":false,"version":1}'), (200, b'{"supported":true,"version":2}')],
)
def test_probe_rejects_incompatible_http_response(status: int, body: bytes) -> None:
    with _capability_server(status=status, body=body) as (port, attempts):
        with pytest.raises(SystemExit, match="managed-identity probe failed"):
            _PROBE.assert_capability(port)
        assert attempts == [None, "probe-managed-identity-key"]


@pytest.mark.parametrize("status", [200, 403, 404, 503])
def test_probe_never_retries_or_accepts_a_wrong_unauthorized_response(status: int) -> None:
    with _capability_server(unauthorized_status=status) as (port, attempts):
        with pytest.raises(SystemExit, match=f"unauthenticated probe status={status}"):
            _PROBE.assert_capability(port)
        assert attempts == [None]


def test_probe_times_out_when_tcp_accepts_but_http_never_becomes_ready() -> None:
    with _capability_server(resets=100) as (port, attempts):
        with pytest.raises(SystemExit, match="HTTP readiness timed out"):
            _PROBE.wait_until_http_ready(port, 0.05)
        assert attempts == [None]


def test_adapted_image_pin_is_the_deployment_default() -> None:
    pin: Final = _IMAGE_REF.load_pin(_ADAPTER)
    assert pin.reference == DEFAULT_CLI_PROXY_IMAGE
    assert pin.reference == "ghcr.io/vvv-345/cliproxyapi:managed-b3c954b63df0-b1d310d5c106"
    assert "bebf587" not in pin.reference
    assert "eceasy" not in pin.reference
    for relative in (
        "account-pool/docker-compose.manager.yml",
        "account-pool/.env.example",
    ):
        text: Final = (_REPO / relative).read_text(encoding="utf-8")
        assert pin.reference in text
        assert "eceasy/cli-proxy-api" not in text
        assert "dead34aa" not in text
    deployment: Final = (_REPO / "deploy/docker-compose.yml").read_text(encoding="utf-8")
    assert "${ACCOUNT_POOL_CLI_PROXY_IMAGE:-ghcr.io/vvv-345/litellm-cliproxyapi:cliproxyapi-${DEPLOY_TAG}}" in deployment
    assert "ACCOUNT_POOL_CLI_PROXY_IMAGE=\n" in (_REPO / "deploy/.env.example").read_text(encoding="utf-8")
    workflow: Final = (_REPO / ".github/workflows/publish-cliproxy-adapted-image.yml").read_text(encoding="utf-8")
    assert "uses: ./.github/workflows/publish-deployment-images.yml" in workflow
    unified: Final = (_REPO / ".github/workflows/publish-deployment-images.yml").read_text(encoding="utf-8")
    assert "image_ref.py" in unified
    assert "probe_managed_identity.py" in unified
    assert "cliproxy-adapter/apply.py" in unified
    assert "cliproxyapi:latest" not in workflow


def test_probe_contract_matches_the_manager_and_keeps_product_retries() -> None:
    rendered: Final = _PROBE.config_text(8317, "/tmp/auths")
    assert _PROBE.CAPABILITY_PATH == "/v0/management/codex/managed-identity"
    assert _PROBE.CAPABILITY_BODY == b'{"supported":true,"version":1}'
    assert _PROBE.MANAGEMENT_HEADER == "X-Management-Key"
    assert "request-retry: 0\n" in rendered
    assert "max-retry-credentials: 1\n" in rendered
    assert "bootstrap-retries: 0\n" in rendered
    assert "request-retry: 3" not in rendered


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url="postgresql://unused",
        data_root=tmp_path,
        manager_token="m" * 32,
        secret_seed="s" * 32,
        ssh_host="example.com",
        ssh_user="operator",
        cli_proxy_image=_NEW_IMAGE,
    )


def _record() -> EnvironmentRecord:
    now: Final = utc_now()
    return EnvironmentRecord(
        id=uuid4(),
        name="Codex card",
        provider=Provider.OPENAI,
        status=EnvironmentStatus.DISABLED,
        enabled=False,
        auth_file_disabled=True,
        manual_cooldown=False,
        concurrency_limit=2,
        proxy_mode=ProxyMode.DEFAULT_GATEWAY,
        proxy_profile_id=None,
        available_models=(),
        enabled_models=(),
        auth_file_name="codex.json",
        auth_index=None,
        quota=QuotaSnapshot(),
        cooldown_until=None,
        oauth_state=None,
        oauth_expires_at=None,
        last_error=None,
        created_at=now,
        updated_at=now,
    )


class _Process:
    def __init__(self, returncode: int = 0, stderr: bytes = b"", stdout: bytes = b"") -> None:
        self._returncode: Final = returncode
        self._stderr: Final = stderr
        self._stdout: Final = stdout
        self.stdin = None

    @property
    def returncode(self) -> int:
        return self._returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        return None


class _Runner:
    def __init__(self, returncode: int = 0, *, running: bool = True) -> None:
        self.returncode: Final = returncode
        self.running = running
        self.calls: list[tuple[str, ...]] = []

    async def __call__(self, arguments: tuple[str, ...], environment: dict[str, str]) -> _Process:
        self.calls.append(arguments)
        if "ps" in arguments:
            return _Process(stdout=b"running\n" if self.running else b"exited\n")
        if "up" in arguments:
            self.running = True
        if "stop" in arguments:
            self.running = False
        return _Process(self.returncode, b"pull failed" if self.returncode else b"")


async def _verified(_record: EnvironmentRecord) -> None:
    return None


async def test_existing_card_switches_to_the_adapted_image_and_keeps_compose_on_failure(tmp_path: Path) -> None:
    settings: Final = _settings(tmp_path)
    record: Final = _record()
    runner: Final = _Runner()
    runtime: Final = ComposeRuntime(settings, EnvironmentSecretDeriver("s" * 32), runner=runner)
    desired: Final = render_compose(record, settings).replace("mem_limit: 512m", "mem_limit: 256m", 1)
    previous: Final = desired.replace(settings.cli_proxy_image, _OLD_IMAGE, 1)
    compose_file: Final = runtime.environment_dir(record.id) / "compose.yaml"
    compose_file.parent.mkdir()
    compose_file.write_text(previous, encoding="utf-8")

    assert await runtime.align_runtime_image(record, verify=_verified) is True

    assert compose_file.read_text(encoding="utf-8") == desired
    assert settings.cli_proxy_image in desired
    assert _OLD_IMAGE not in compose_file.read_text(encoding="utf-8")
    command: Final = next(call for call in runner.calls if "up" in call)
    assert command[:2] == ("docker", "compose")
    assert command[command.index("up") :] == ("up", "-d", "--pull", "never", "--no-deps", "cli-proxy-api")
    assert runner.running is True
    assert record.enabled is False
    assert record.auth_file_disabled is True


async def test_same_image_does_not_recreate_the_card(tmp_path: Path) -> None:
    settings: Final = _settings(tmp_path)
    record: Final = _record()
    runner: Final = _Runner()
    runtime: Final = ComposeRuntime(settings, EnvironmentSecretDeriver("s" * 32), runner=runner)
    desired: Final = render_compose(record, settings).replace("mem_limit: 512m", "mem_limit: 256m", 1)
    compose_file: Final = runtime.environment_dir(record.id) / "compose.yaml"
    compose_file.parent.mkdir()
    compose_file.write_text(desired, encoding="utf-8")

    assert await runtime.align_runtime_image(record, verify=_verified) is False

    assert compose_file.read_text(encoding="utf-8") == desired
    assert runner.calls == []


async def test_failed_pull_restores_the_previous_card_image(tmp_path: Path) -> None:
    settings: Final = _settings(tmp_path)
    record: Final = _record()
    runtime: Final = ComposeRuntime(settings, EnvironmentSecretDeriver("s" * 32), runner=_Runner(returncode=1))
    desired: Final = render_compose(record, settings)
    previous: Final = desired.replace(settings.cli_proxy_image, _OLD_IMAGE, 1)
    compose_file: Final = runtime.environment_dir(record.id) / "compose.yaml"
    compose_file.parent.mkdir()
    compose_file.write_text(previous, encoding="utf-8")

    try:
        await runtime.align_runtime_image(record, verify=_verified)
    except RuntimeError as error:
        assert "pull failed" in str(error)
    else:
        raise AssertionError("image pull failure must surface")

    assert compose_file.read_text(encoding="utf-8") == previous


@pytest.mark.parametrize("running", [True, False])
@pytest.mark.parametrize("probe_fails", [True, False])
async def test_card_update_preserves_runtime_state_and_compensates_failed_probe(
    tmp_path: Path, running: bool, probe_fails: bool
) -> None:
    settings = _settings(tmp_path)
    record = _record()
    runner = _Runner(running=running)
    runtime = ComposeRuntime(settings, EnvironmentSecretDeriver("s" * 32), runner=runner)
    previous = render_compose(record, settings).replace(_NEW_IMAGE, _OLD_IMAGE)
    compose_file = runtime.environment_dir(record.id) / "compose.yaml"
    compose_file.parent.mkdir()
    compose_file.write_text(previous, encoding="utf-8")

    async def verify(current: EnvironmentRecord) -> None:
        assert current.enabled is False
        assert runner.running is True
        if probe_fails:
            raise ValueError("unsupported capability")

    if probe_fails:
        with pytest.raises(ValueError, match="unsupported capability"):
            await runtime.align_runtime_image(record, verify=verify)
    else:
        assert await runtime.align_runtime_image(record, verify=verify) is True

    assert runner.running is running
    restored = compose_file.read_text(encoding="utf-8")
    assert restored == (previous if probe_fails else previous.replace(_OLD_IMAGE, _NEW_IMAGE))
    assert yaml.safe_load(restored)["volumes"] == yaml.safe_load(previous)["volumes"]
    assert all("volume" not in call and "down" not in call and "run" not in call for call in runner.calls)


async def test_card_update_refuses_enabled_card_before_docker_or_file_write(tmp_path: Path) -> None:
    runner = _Runner()
    runtime = ComposeRuntime(_settings(tmp_path), EnvironmentSecretDeriver("s" * 32), runner=runner)
    with pytest.raises(ValueError, match="disabled"):
        await runtime.align_runtime_image(_record().model_copy(update={"enabled": True}), verify=_verified)
    assert runner.calls == []
