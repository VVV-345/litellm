"""CLIProxyAPI runtime owns trusted rendering and low-level Docker execution."""

from __future__ import annotations

from pathlib import Path
from typing import Final
from uuid import UUID

from account_pool.compose_renderer import render_cli_proxy_config, render_compose
from account_pool.compose_runtime import ComposeRuntime
from account_pool.domain import EnvironmentRecord
from account_pool.secrets import EnvironmentSecretDeriver, SecretPurpose


class CLIProxyAPIRuntime:
    def __init__(self, runtime: ComposeRuntime, secrets: EnvironmentSecretDeriver) -> None:
        self._runtime: Final = runtime
        self._secrets: Final = secrets

    async def provision(self, record: EnvironmentRecord) -> None:
        management_key: Final = self._secrets.derive(record.id, SecretPurpose.MANAGEMENT)
        gateway_key: Final = self._secrets.derive(record.id, SecretPurpose.GATEWAY)
        await self._runtime.provision(
            record,
            compose=render_compose(record, self._runtime.settings),
            config=render_cli_proxy_config(management_key, gateway_key),
        )

    async def ensure_control_plane_connections(self, environment_id: UUID) -> None:
        await self._runtime.ensure_control_plane_connections(environment_id)

    async def set_running(self, record: EnvironmentRecord, running: bool) -> None:
        await self._runtime.set_running(record, running)

    async def remove(self, record: EnvironmentRecord) -> None:
        await self._runtime.remove(record)

    async def remove_compose(self, record: EnvironmentRecord) -> None:
        await self._runtime.remove_compose(record)

    async def remove_directory(self, environment_id: UUID) -> None:
        await self._runtime.remove_directory(environment_id)

    def environment_dir(self, environment_id: UUID) -> Path:
        return self._runtime.environment_dir(environment_id)
