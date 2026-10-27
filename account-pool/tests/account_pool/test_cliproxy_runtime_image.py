"""托管安装 ID 只在补丁镜像上生效，旧卡片要换成这个镜像后才重建。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Final
from uuid import uuid4

from account_pool.compose_renderer import render_compose
from account_pool.compose_runtime import ComposeRuntime
from account_pool.config import DEFAULT_CLI_PROXY_IMAGE, Settings
from account_pool.domain import EnvironmentRecord, EnvironmentStatus, Provider, ProxyMode, QuotaSnapshot, utc_now
from account_pool.shared.secrets import EnvironmentSecretDeriver

_ADAPTER: Final = Path(__file__).resolve().parents[2] / "cliproxy-adapter"
_REPO: Final = Path(__file__).resolve().parents[3]
_OLD_IMAGE: Final = "ghcr.io/vvv-345/cliproxyapi:sha-bebf587f5a940af676f6e503065140d4991ae1f3"


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


def test_adapted_image_pin_is_the_deployment_default() -> None:
    pin: Final = _IMAGE_REF.load_pin(_ADAPTER)
    assert pin.reference == DEFAULT_CLI_PROXY_IMAGE
    assert pin.reference == "ghcr.io/vvv-345/cliproxyapi:managed-b3c954b63df0-b1d310d5c106"
    assert "bebf587" not in pin.reference
    assert "eceasy" not in pin.reference
    for relative in (
        "account-pool/docker-compose.manager.yml",
        "account-pool/.env.example",
        "deploy/docker-compose.yml",
        "deploy/.env.example",
    ):
        text: Final = (_REPO / relative).read_text(encoding="utf-8")
        assert pin.reference in text
        assert "eceasy/cli-proxy-api" not in text
        assert "dead34aa" not in text
    workflow: Final = (_REPO / ".github/workflows/publish-cliproxy-adapted-image.yml").read_text(encoding="utf-8")
    assert "image_ref.py" in workflow
    assert "probe_managed_identity.py" in workflow
    assert "*:latest|*bebf587*|eceasy/*" in workflow
    assert "refusing to replace an existing adapted image tag" in workflow
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
        cli_proxy_image=DEFAULT_CLI_PROXY_IMAGE,
    )


def _record() -> EnvironmentRecord:
    now: Final = utc_now()
    return EnvironmentRecord(
        id=uuid4(),
        name="Codex card",
        provider=Provider.OPENAI,
        status=EnvironmentStatus.READY,
        enabled=True,
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
    def __init__(self, returncode: int = 0, stderr: bytes = b"") -> None:
        self._returncode: Final = returncode
        self._stderr: Final = stderr
        self.stdin = None

    @property
    def returncode(self) -> int:
        return self._returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", self._stderr

    def kill(self) -> None:
        return None


class _Runner:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode: Final = returncode
        self.calls: list[tuple[str, ...]] = []

    async def __call__(self, arguments: tuple[str, ...], environment: dict[str, str]) -> _Process:
        self.calls.append(arguments)
        return _Process(self.returncode, b"pull failed" if self.returncode else b"")


async def test_existing_card_switches_to_the_adapted_image_and_keeps_compose_on_failure(tmp_path: Path) -> None:
    settings: Final = _settings(tmp_path)
    record: Final = _record()
    runner: Final = _Runner()
    runtime: Final = ComposeRuntime(settings, EnvironmentSecretDeriver("s" * 32), runner=runner)
    desired: Final = render_compose(record, settings)
    previous: Final = desired.replace(settings.cli_proxy_image, _OLD_IMAGE, 1)
    compose_file: Final = runtime.environment_dir(record.id) / "compose.yaml"
    compose_file.parent.mkdir()
    compose_file.write_text(previous, encoding="utf-8")

    assert await runtime.align_runtime_image(record) is True

    assert compose_file.read_text(encoding="utf-8") == desired
    assert settings.cli_proxy_image in desired
    assert _OLD_IMAGE not in compose_file.read_text(encoding="utf-8")
    command: Final = runner.calls[0]
    assert command[:2] == ("docker", "compose")
    assert command[command.index("up") : command.index("up") + 4] == ("up", "-d", "--pull", "always")


async def test_same_image_does_not_recreate_the_card(tmp_path: Path) -> None:
    settings: Final = _settings(tmp_path)
    record: Final = _record()
    runner: Final = _Runner()
    runtime: Final = ComposeRuntime(settings, EnvironmentSecretDeriver("s" * 32), runner=runner)
    desired: Final = render_compose(record, settings).replace("mem_limit: 512m", "mem_limit: 256m", 1)
    compose_file: Final = runtime.environment_dir(record.id) / "compose.yaml"
    compose_file.parent.mkdir()
    compose_file.write_text(desired, encoding="utf-8")

    assert await runtime.align_runtime_image(record) is False

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
        await runtime.align_runtime_image(record)
    except RuntimeError as error:
        assert "pull failed" in str(error)
    else:
        raise AssertionError("image pull failure must surface")

    assert compose_file.read_text(encoding="utf-8") == previous
