"""本模块执行号池 Docker Compose 和网络生命周期，不负责配置渲染。"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Final, Protocol
from uuid import UUID

from account_pool.compose_renderer import data_volume_name, render_cli_proxy_config, render_compose
from account_pool.config import Settings
from account_pool.domain import EnvironmentRecord
from account_pool.secrets import EnvironmentSecretDeriver, SecretPurpose


class DockerProcess(Protocol):
    @property
    def returncode(self) -> int | None: ...

    @property
    def stdin(self) -> asyncio.StreamWriter | None: ...

    async def communicate(self) -> tuple[bytes, bytes]: ...

    def kill(self) -> None: ...


DockerRunner = Callable[[tuple[str, ...], dict[str, str]], Awaitable[DockerProcess]]

# 仅用于 chown 数据卷的固定 alpine 版本，digest 与 cli_proxy_image 同等锁定。
_CHOWN_IMAGE: Final = "alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc"


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

    @property
    def settings(self) -> Settings:
        return self._settings

    async def provision(
        self,
        record: EnvironmentRecord,
        *,
        compose: str | None = None,
        config: str | None = None,
    ) -> None:
        environment_dir: Final = self.environment_dir(record.id)
        environment_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        selected_compose: Final = compose or render_compose(record, self._settings)
        selected_config: Final = config or render_cli_proxy_config(
            self._secrets.derive(record.id, SecretPurpose.MANAGEMENT),
            self._secrets.derive(record.id, SecretPurpose.GATEWAY),
        )
        _write_private(environment_dir / "compose.yaml", selected_compose)
        await self._create_data_volume(record.id)
        await self._write_data_volume(record.id, selected_config)
        await self._compose(record.id, "up", "-d", "--pull", "always", "--remove-orphans")
        await self.ensure_control_plane_connections(record.id)

    async def provision_freebuff(self, record: EnvironmentRecord, *, compose: str) -> None:
        environment_dir: Final = self.environment_dir(record.id)
        environment_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        _write_private(environment_dir / "compose.yaml", compose)
        await self._create_data_volume(record.id)
        await self._seed_freebuff_data_volume(record.id)
        await self._compose(record.id, "up", "-d", "--pull", "always", "--remove-orphans")
        await self.ensure_control_plane_connections(record.id)

    async def _seed_freebuff_data_volume(self, environment_id: UUID) -> None:
        volume: Final = data_volume_name(environment_id)
        chown: Final = await self._runner(
            (
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "-v",
                f"{volume}:/data:rw",
                _CHOWN_IMAGE,
                "chown",
                "1000:1000",
                "/data",
            ),
            self._docker_environment(),
        )
        chown_stdout, chown_stderr = await communicate_with_timeout(chown, self._settings.docker_command_timeout_seconds)
        if chown.returncode != 0:
            detail: Final = chown_stderr.decode("utf-8", errors="replace").strip() or chown_stdout.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"failed to chown {volume}: {detail[:500]}")

    async def _create_data_volume(self, environment_id: UUID) -> None:
        volume: Final = data_volume_name(environment_id)
        # 卷必须先于 compose up 存在，否则 compose 会把外部卷当成本项目资源，down 时一并删除。
        # 新卷根目录归 root，先用一次性 root 容器把所有权交给运行账号容器 UID，再由它写入配置。
        create: Final = await self._runner(
            ("docker", "volume", "create", "--label", f"account-pool-environment={environment_id.hex}", volume),
            self._docker_environment(),
        )
        stdout, stderr = await communicate_with_timeout(create, self._settings.docker_command_timeout_seconds)
        if create.returncode != 0:
            detail: Final = stderr.decode("utf-8", errors="replace").strip() or stdout.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"failed to create {volume}: {detail[:500]}")
        chown: Final = await self._runner(
            (
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "-v",
                f"{volume}:/data:rw",
                _CHOWN_IMAGE,
                "chown",
                self._settings.cli_proxy_user,
                "/data",
            ),
            self._docker_environment(),
        )
        chown_stdout, chown_stderr = await communicate_with_timeout(chown, self._settings.docker_command_timeout_seconds)
        if chown.returncode != 0:
            detail: Final = chown_stderr.decode("utf-8", errors="replace").strip() or chown_stdout.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"failed to chown {volume}: {detail[:500]}")

    async def _write_data_volume(self, environment_id: UUID, config: str) -> None:
        volume: Final = data_volume_name(environment_id)
        # 用一次性容器经受控 Docker API 写入，Manager 自身不挂载宿主机数据路径。
        arguments: Final = (
            "docker",
            "run",
            "--rm",
            "--name",
            f"account-pool-{environment_id.hex}-seed",
            "--network",
            "none",
            "--user",
            self._settings.cli_proxy_user,
            "-v",
            f"{volume}:/data:rw",
            self._settings.cli_proxy_image,
            "sh",
            "-c",
            f"mkdir -p /data/config /data/auths && printf '%s' {shlex.quote(config)} > /data/config/config.yaml && chmod 700 /data/config /data/auths && chmod 600 /data/config/config.yaml",
        )
        process: Final = await self._runner(arguments, self._docker_environment())
        stdout, stderr = await communicate_with_timeout(process, self._settings.docker_command_timeout_seconds)
        if process.returncode != 0:
            detail: Final = stderr.decode("utf-8", errors="replace").strip() or stdout.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"failed to seed {volume}: {detail[:500]}")

    async def ensure_control_plane_connections(self, environment_id: UUID) -> None:
        await self._connect_control_plane(environment_id, self._settings.manager_container)
        await self._connect_control_plane(environment_id, self._settings.gateway_container)

    async def write_volume_files(
        self,
        environment_id: UUID,
        image: str,
        script: str,
        stdin_content: str,
        *,
        user: str | None = None,
    ) -> None:
        """在一次性容器里执行 script，通过 stdin 传入敏感内容，避免拼进命令行参数。"""
        volume: Final = data_volume_name(environment_id)
        arguments: Final = (
            "docker",
            "run",
            "--rm",
            "--name",
            f"account-pool-{environment_id.hex}-seed",
            "--network",
            "none",
            "--user",
            user or self._settings.cli_proxy_user,
            "--interactive",
            "--entrypoint",
            "sh",
            "-v",
            f"{volume}:/data:rw",
            image,
            "-c",
            script,
        )
        process: Final = await self._runner(arguments, self._docker_environment())
        try:
            await asyncio.wait_for(
                self._write_stdin(process, stdin_content),
                timeout=self._settings.docker_command_timeout_seconds,
            )
        except (TimeoutError, RuntimeError) as error:
            process.kill()
            raise RuntimeError(f"failed to write {volume} seed files: {error}") from error
        stdout, stderr = await communicate_with_timeout(process, self._settings.docker_command_timeout_seconds)
        if process.returncode != 0:
            detail: Final = stderr.decode("utf-8", errors="replace").strip() or stdout.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"failed to write {volume} seed files: {detail[:500]}")

    async def restart(self, environment_id: UUID) -> None:
        await self._compose(environment_id, "restart")

    async def _write_stdin(self, process: DockerProcess, stdin_content: str) -> None:
        stdin = process.stdin
        if stdin is None:
            raise RuntimeError("docker process stdin is unavailable")
        try:
            stdin.write(stdin_content.encode("utf-8"))
            await stdin.drain()
            stdin.close()
            await stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError, OSError) as error:
            raise RuntimeError("docker process closed stdin early") from error

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
        await self._remove_data_volume(record.id)

    async def _remove_data_volume(self, environment_id: UUID) -> None:
        volume: Final = data_volume_name(environment_id)
        process: Final = await self._runner(
            ("docker", "volume", "rm", "--force", volume),
            self._docker_environment(),
        )
        stdout, stderr = await communicate_with_timeout(process, self._settings.docker_command_timeout_seconds)
        detail: Final = stderr.decode("utf-8", errors="replace").strip() or stdout.decode("utf-8", errors="replace").strip()
        absent: Final = "no such volume" in detail.lower()
        if process.returncode != 0 and not absent:
            raise RuntimeError(f"failed to remove {volume}: {detail[:500]}")

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
        absent: Final = (
            "is not connected" in detail.lower()
            or "no such container" in detail.lower()
            or re.search(r"network \S+ not found", detail, re.IGNORECASE) is not None
        )
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
        # 子进程环境不能省略 PATH，否则 exec 找不到内嵌的 docker CLI。
        path: str | None = os.environ.get("PATH")
        if path is None:
            return {"DOCKER_HOST": self._settings.docker_host, "HOME": "/tmp"}
        return {"DOCKER_HOST": self._settings.docker_host, "HOME": "/tmp", "PATH": path}

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
