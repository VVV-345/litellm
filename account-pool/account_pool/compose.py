"""本模块安全渲染并执行每个账号独立的 Docker Compose 环境。"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Final
from uuid import UUID

import yaml

from account_pool.config import Settings
from account_pool.domain import EnvironmentRecord
from account_pool.secrets import EnvironmentSecretDeriver, SecretPurpose


def render_cli_proxy_config(management_key: str, gateway_key: str) -> str:
    config: Final = {
        "host": "0.0.0.0",
        "port": 8317,
        "remote-management": {
            "allow-remote": True,
            "secret-key": management_key,
            "disable-control-panel": True,
        },
        "auth-dir": "/data/auths",
        "api-keys": [gateway_key],
        "debug": False,
        "logging-to-file": False,
        "usage-statistics-enabled": False,
        "save-cooldown-status": True,
        "proxy-url": "",
        "ws-auth": True,
    }
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=False)


def render_compose(record: EnvironmentRecord, settings: Settings) -> str:
    environment_slug: Final = record.id.hex
    service_name: Final = f"cliproxy-{environment_slug}"
    network_name: Final = f"account-pool-{environment_slug}"
    compose: Final = {
        "name": f"account-pool-{environment_slug}",
        "services": {
            "cli-proxy-api": {
                "image": settings.cli_proxy_image,
                "command": ["./CLIProxyAPI", "-config", "/data/config/config.yaml"],
                "restart": "unless-stopped",
                "read_only": True,
                "user": settings.cli_proxy_user,
                "mem_limit": "512m",
                "cpus": "1.0",
                "pids_limit": 256,
                "ulimits": {"nofile": {"soft": 4096, "hard": 4096}},
                "logging": {
                    "driver": "json-file",
                    "options": {"max-size": "10m", "max-file": "3"},
                },
                "security_opt": ["no-new-privileges:true"],
                "cap_drop": ["ALL"],
                "tmpfs": ["/tmp:rw,noexec,nosuid,size=32m"],
                "volumes": [
                    "./config:/data/config:rw",
                    "./auths:/data/auths:rw",
                ],
                "networks": {"environment": {"aliases": [service_name]}},
            }
        },
        "networks": {
            "environment": {"name": network_name, "driver": "bridge"},
        },
    }
    return yaml.safe_dump(compose, sort_keys=False, allow_unicode=False)


class ComposeRuntime:
    def __init__(self, settings: Settings, secrets: EnvironmentSecretDeriver) -> None:
        self._settings: Final = settings
        self._secrets: Final = secrets

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
        # 管理服务或网关重启后，Docker 会移除旧网络端点，这里按环境幂等恢复连接。
        await self._connect_control_plane(environment_id, self._settings.manager_container)
        await self._connect_control_plane(environment_id, self._settings.gateway_container)

    async def set_running(self, record: EnvironmentRecord, running: bool) -> None:
        command: Final = ("start",) if running else ("stop",)
        await self._compose(record.id, *command)

    async def remove(
        self,
        project_or_record: EnvironmentRecord | str,
        environment_directory: Path | None = None,
    ) -> None:
        """删除 Compose 资源；兼容记录调用和显式 project/path 调用。"""
        if isinstance(project_or_record, EnvironmentRecord):
            await self.remove_compose(project_or_record)
            await self.remove_directory(project_or_record.id)
            return
        project_name: Final = project_or_record
        directory: Final = environment_directory
        if directory is None:
            raise ValueError("environment directory is required")
        _validate_project_directory(project_name, directory, self._settings.data_root)
        await self._disconnect_control_plane_by_network(project_name.removeprefix("account-pool-"))
        await self._compose_at(project_name, directory, "down", "--remove-orphans")
        _remove_environment_directory(directory, self._settings.data_root)

    async def remove_compose(self, record: EnvironmentRecord) -> None:
        await self._disconnect_control_plane(record.id, self._settings.manager_container)
        await self._disconnect_control_plane(record.id, self._settings.gateway_container)
        await self._compose(record.id, "down", "--remove-orphans")

    async def remove_directory(self, environment_id: UUID) -> None:
        await asyncio.to_thread(
            _remove_environment_directory,
            self.environment_dir(environment_id),
            self._settings.data_root,
        )

    async def _connect_control_plane(self, environment_id: UUID, container: str) -> None:
        network: Final = f"account-pool-{environment_id.hex}"
        process: Final = await asyncio.create_subprocess_exec(
            "docker",
            "network",
            "connect",
            network,
            container,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "DOCKER_HOST": self._settings.docker_host},
        )
        stdout, stderr = await _communicate_with_timeout(process, self._settings.docker_command_timeout_seconds)
        detail: Final = (
            stderr.decode("utf-8", errors="replace").strip() or stdout.decode("utf-8", errors="replace").strip()
        )
        already_connected: Final = "already exists in network" in detail.lower()
        if process.returncode != 0 and not already_connected:
            raise RuntimeError(f"failed to attach {container} to {network}: {detail[:500]}")

    async def _disconnect_control_plane(self, environment_id: UUID, container: str) -> None:
        network: Final = f"account-pool-{environment_id.hex}"
        process: Final = await asyncio.create_subprocess_exec(
            "docker",
            "network",
            "disconnect",
            network,
            container,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "DOCKER_HOST": self._settings.docker_host},
        )
        stdout, stderr = await _communicate_with_timeout(process, self._settings.docker_command_timeout_seconds)
        detail: Final = (
            stderr.decode("utf-8", errors="replace").strip() or stdout.decode("utf-8", errors="replace").strip()
        )
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
        process: Final = await asyncio.create_subprocess_exec(
            "docker",
            "compose",
            "--project-name",
            project_name,
            "--file",
            str(compose_file),
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "DOCKER_HOST": self._settings.docker_host},
        )
        stdout, stderr = await _communicate_with_timeout(process, self._settings.docker_command_timeout_seconds)
        if process.returncode != 0:
            detail: Final = (
                stderr.decode("utf-8", errors="replace").strip() or stdout.decode("utf-8", errors="replace").strip()
            )
            raise RuntimeError(f"docker compose failed: {detail[:500]}")

    async def _disconnect_control_plane_by_network(self, slug: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{32}", slug):
            raise ValueError("invalid account pool project name")
        await self._disconnect_control_plane(UUID(hex=slug), self._settings.manager_container)
        await self._disconnect_control_plane(UUID(hex=slug), self._settings.gateway_container)


def _write_private(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(0o600)


async def _communicate_with_timeout(
    process: asyncio.subprocess.Process,
    timeout_seconds: float,
) -> tuple[bytes, bytes]:
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
