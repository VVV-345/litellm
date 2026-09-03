"""本模块执行号池 Docker Compose 和网络生命周期，不负责配置渲染。"""

from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Final, Protocol
from uuid import UUID

from account_pool.compose_renderer import render_cli_proxy_config, render_compose
from account_pool.config import Settings
from account_pool.domain import EnvironmentRecord
from account_pool.secrets import EnvironmentSecretDeriver, SecretPurpose


class DockerProcess(Protocol):
    @property
    def returncode(self) -> int | None: ...

    async def communicate(self) -> tuple[bytes, bytes]: ...

    def kill(self) -> None: ...


DockerRunner = Callable[[tuple[str, ...], dict[str, str]], Awaitable[DockerProcess]]


async def run_docker(arguments: tuple[str, ...], environment: dict[str, str]) -> DockerProcess:
    return await asyncio.create_subprocess_exec(
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
    )


class ComposeRuntime:
    def __init__(
        self,
        settings: Settings,
        secrets: EnvironmentSecretDeriver,
        *,
        runner: DockerRunner = run_docker,
    ) -> None:
        self._settings: Final = settings
        self._secrets: Final = secrets
        self._runner: Final = runner

    def environment_dir(self, environment_id: UUID) -> Path:
        return self._settings.data_root / environment_id.hex

    async def provision(self, record: EnvironmentRecord) -> None:
        environment_dir: Final = self.environment_dir(record.id)
        config_dir: Final = environment_dir / "config"
        auth_dir: Final = environment_dir / "auths"
        environment_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        auth_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        management_key: Final = self._secrets.derive(record.id, SecretPurpose.MANAGEMENT)
        gateway_key: Final = self._secrets.derive(record.id, SecretPurpose.GATEWAY)
        _write_private(config_dir / "config.yaml", render_cli_proxy_config(management_key, gateway_key))
        _write_private(environment_dir / "compose.yaml", render_compose(record, self._settings))
        await self._compose(record.id, "up", "-d", "--pull", "always", "--remove-orphans")
        await self.ensure_control_plane_connections(record.id)

    async def ensure_control_plane_connections(self, environment_id: UUID) -> None:
        await self._connect_control_plane(environment_id, self._settings.manager_container)
        await self._connect_control_plane(environment_id, self._settings.gateway_container)

    async def set_running(self, record: EnvironmentRecord, running: bool) -> None:
        command: Final = ("start",) if running else ("stop",)
        await self._compose(record.id, *command)

    async def remove(self, record: EnvironmentRecord) -> None:
        await self.remove_compose(record)
        await self.remove_directory(record.id)

    async def remove_compose(self, record: EnvironmentRecord) -> None:
        await self._disconnect_control_plane(record.id, self._settings.manager_container)
        await self._disconnect_control_plane(record.id, self._settings.gateway_container)
        await self._compose(record.id, "down", "--remove-orphans")

    async def remove_legacy(self, project_name: str, directory: Path) -> None:
        _validate_project_directory(project_name, directory, self._settings.data_root)
        await self._disconnect_control_plane_by_network(project_name.removeprefix("account-pool-"))
        await self._compose_at(project_name, directory, "down", "--remove-orphans")
        _remove_environment_directory(directory, self._settings.data_root)

    async def remove_directory(self, environment_id: UUID) -> None:
        await asyncio.to_thread(
            _remove_environment_directory,
            self.environment_dir(environment_id),
            self._settings.data_root,
        )

    async def _connect_control_plane(self, environment_id: UUID, container: str) -> None:
        network: Final = f"account-pool-{environment_id.hex}"
        process: Final = await self._runner(("docker", "network", "connect", network, container), self._docker_environment())
        stdout, stderr = await communicate_with_timeout(process, self._settings.docker_command_timeout_seconds)
        detail: Final = stderr.decode("utf-8", errors="replace").strip() or stdout.decode("utf-8", errors="replace").strip()
        already_connected: Final = "already exists in network" in detail.lower()
        if process.returncode != 0 and not already_connected:
            raise RuntimeError(f"failed to attach {container} to {network}: {detail[:500]}")

    async def _disconnect_control_plane(self, environment_id: UUID, container: str) -> None:
        network: Final = f"account-pool-{environment_id.hex}"
        process: Final = await self._runner(("docker", "network", "disconnect", network, container), self._docker_environment())
        stdout, stderr = await communicate_with_timeout(process, self._settings.docker_command_timeout_seconds)
        detail: Final = stderr.decode("utf-8", errors="replace").strip() or stdout.decode("utf-8", errors="replace").strip()
        absent: Final = "is not connected" in detail.lower() or "no such container" in detail.lower()
        if process.returncode != 0 and not absent:
            raise RuntimeError(f"failed to detach {container} from {network}: {detail[:500]}")

    async def _compose(self, environment_id: UUID, *arguments: str) -> None:
        compose_file: Final = self.environment_dir(environment_id) / "compose.yaml"
        await self._compose_at(f"account-pool-{environment_id.hex}", compose_file.parent, *arguments)

    async def _compose_at(self, project_name: str, environment_dir: Path, *arguments: str) -> None:
        compose_file: Final = environment_dir / "compose.yaml"
        if not compose_file.exists() and arguments and arguments[0] == "down":
            return
        process: Final = await self._runner(
            ("docker", "compose", "--project-name", project_name, "--file", str(compose_file), *arguments),
            self._docker_environment(),
        )
        stdout, stderr = await communicate_with_timeout(process, self._settings.docker_command_timeout_seconds)
        if process.returncode != 0:
            detail: Final = stderr.decode("utf-8", errors="replace").strip() or stdout.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"docker compose failed: {detail[:500]}")

    def _docker_environment(self) -> dict[str, str]:
        return {"DOCKER_HOST": self._settings.docker_host, "HOME": "/tmp"}

    async def _disconnect_control_plane_by_network(self, slug: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{32}", slug):
            raise ValueError("invalid account pool project name")
        await self._disconnect_control_plane(UUID(hex=slug), self._settings.manager_container)
        await self._disconnect_control_plane(UUID(hex=slug), self._settings.gateway_container)


def _write_private(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(0o600)


async def communicate_with_timeout(process: DockerProcess, timeout_seconds: float) -> tuple[bytes, bytes]:
    try:
        return await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError as error:
        process.kill()
        await process.communicate()
        raise RuntimeError("Docker command timed out") from error


def _remove_environment_directory(environment_dir: Path, data_root: Path) -> None:
    root: Final = data_root.resolve()
    target: Final = environment_dir.resolve()
    if target.parent != root or target.name == "":
        raise RuntimeError("refusing to remove an environment directory outside the configured data root")
    if target.is_symlink():
        target.unlink()
    elif target.exists():
        shutil.rmtree(target)


def _validate_project_directory(project_name: str, environment_dir: Path, data_root: Path) -> None:
    if not re.fullmatch(r"account-pool-[0-9a-f]{32}", project_name):
        raise ValueError("invalid account pool project name")
    root: Final = data_root.resolve()
    target: Final = environment_dir.resolve()
    if target.parent != root or target.name != project_name.removeprefix("account-pool-"):
        raise ValueError("project name and environment directory do not match")
