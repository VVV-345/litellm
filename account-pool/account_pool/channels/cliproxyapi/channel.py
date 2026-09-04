"""CLIProxyAPI channel composition root."""

from __future__ import annotations

from pathlib import Path
from typing import Final
from uuid import UUID

from account_pool.channels.cliproxyapi.client import AuthorizationStart, HttpCLIProxyClient
from account_pool.channels.cliproxyapi.suppliers.base import SupplierDefinition
from account_pool.channels.cliproxyapi.suppliers.registry import SupplierRegistry
from account_pool.compose_renderer import render_cli_proxy_config, render_compose
from account_pool.compose_runtime import ComposeRuntime
from account_pool.config import Settings
from account_pool.domain import EnvironmentConfiguration, EnvironmentRecord, GatewayEnvironment, OAuthCallback, SupplierKind
from account_pool.secrets import EnvironmentSecretDeriver, SecretPurpose


class CLIProxyAPIChannel:
    def __init__(
        self,
        settings: Settings,
        secrets: EnvironmentSecretDeriver,
        *,
        runtime: ComposeRuntime | None = None,
        client: HttpCLIProxyClient | None = None,
        suppliers: SupplierRegistry | None = None,
    ) -> None:
        self._settings: Final = settings
        self._secrets: Final = secrets
        self._runtime: Final = runtime or ComposeRuntime(settings, secrets)
        self._client: Final = client or HttpCLIProxyClient(secrets)
        self._suppliers: Final = suppliers or SupplierRegistry.default()

    def supplier(self, kind: SupplierKind) -> SupplierDefinition:
        return self._suppliers.get(kind)

    async def close(self) -> None:
        await self._client.close()

    async def provision(self, record: EnvironmentRecord) -> None:
        management_key: Final = self._secrets.derive(record.id, SecretPurpose.MANAGEMENT)
        gateway_key: Final = self._secrets.derive(record.id, SecretPurpose.GATEWAY)
        await self._runtime.provision(
            record,
            compose=render_compose(record, self._settings),
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

    async def start_openai_authorization(self, record: EnvironmentRecord) -> tuple[str, str]:
        result: Final = await self._client.start_authorization(record, self.supplier(SupplierKind.OPENAI_CODEX))
        return result.authorization_url, result.provider_state

    async def start_authorization(self, record: EnvironmentRecord) -> AuthorizationStart:
        return await self._client.start_authorization(record, self.supplier(record.supplier))

    async def authorization_status(self, record: EnvironmentRecord, state: str) -> str:
        return await self._client.authorization_status(record, state)

    async def submit_callback(self, record: EnvironmentRecord, callback: OAuthCallback) -> None:
        await self._client.submit_callback(record, self.supplier(record.supplier), callback)

    async def read_account(self, record: EnvironmentRecord) -> EnvironmentRecord:
        return await self._client.read_account(record, self.supplier(record.supplier))

    async def data_plane_health_check(self, record: EnvironmentRecord) -> bool:
        return await self._client.data_plane_health_check(record)

    async def apply_configuration(self, record: EnvironmentRecord, configuration: EnvironmentConfiguration) -> None:
        await self._client.apply_configuration(record, self.supplier(record.supplier), configuration)

    def gateway(self, record: EnvironmentRecord) -> GatewayEnvironment:
        return GatewayEnvironment(
            id=record.id,
            routable=record.status.value == "ready"
            and record.enabled
            and not record.manual_cooldown
            and record.cooldown_until is None
            and not record.configuration_pending
            and record.desired_configuration_version <= record.observed_configuration_version,
            concurrency_limit=record.concurrency_limit,
            enabled_models=record.enabled_models,
            api_base=f"http://cliproxy-{record.id.hex}:8317/v1",
            api_key=self._secrets.derive(record.id, SecretPurpose.GATEWAY),
            custom_llm_provider="openai",
        )


__all__ = ("CLIProxyAPIChannel",)
