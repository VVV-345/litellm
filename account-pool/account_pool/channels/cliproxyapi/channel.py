"""CLIProxyAPI channel composition root."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Final
from uuid import UUID

from account_pool.channels.cliproxyapi.client import AuthorizationStart, HttpCLIProxyClient
from account_pool.channels.cliproxyapi.settings_sync import CLIProxySettingsSynchronizer
from account_pool.channels.cliproxyapi.suppliers.base import SupplierDefinition
from account_pool.channels.cliproxyapi.suppliers.registry import SupplierRegistry
from account_pool.compose_renderer import render_cli_proxy_config, render_compose
from account_pool.compose_runtime import ComposeRuntime
from account_pool.config import Settings
from account_pool.domain import (
    DirectAPIKeyCredentialRequest,
    EnvironmentConfiguration,
    EnvironmentRecord,
    GatewayEnvironment,
    OAuthCallback,
    SupplierKind,
)
from account_pool.policies import AccountPolicy
from account_pool.secrets import EnvironmentSecretDeriver, SecretPurpose
from account_pool.settings import AccountPoolSettings


class CLIProxyAPIChannel:
    def __init__(
        self,
        settings: Settings,
        secrets: EnvironmentSecretDeriver,
        *,
        runtime: ComposeRuntime | None = None,
        client: HttpCLIProxyClient | None = None,
        settings_sync: CLIProxySettingsSynchronizer | None = None,
        suppliers: SupplierRegistry | None = None,
    ) -> None:
        self._settings: Final = settings
        self._secrets: Final = secrets
        self._runtime: Final = runtime or ComposeRuntime(settings, secrets)
        self._client: Final = client or HttpCLIProxyClient(secrets)
        self._suppliers: Final = suppliers or SupplierRegistry.default()
        self._settings_sync: Final = settings_sync or CLIProxySettingsSynchronizer(self._client, self._suppliers)

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

    async def cancel_oauth_session(self, record: EnvironmentRecord, state: str) -> None:
        await self._client.cancel_oauth_session(record, state)

    async def submit_callback(self, record: EnvironmentRecord, callback: OAuthCallback) -> None:
        await self._client.submit_callback(record, self.supplier(record.supplier), callback)

    async def read_account(self, record: EnvironmentRecord) -> EnvironmentRecord:
        return await self._client.read_account(record, self.supplier(record.supplier))

    async def write_direct_api_key(
        self, record: EnvironmentRecord, credential: DirectAPIKeyCredentialRequest, proxy_url: str
    ) -> None:
        await self._client.write_direct_api_key(
            record,
            self.supplier(record.supplier),
            api_key=credential.api_key,
            prefix=credential.prefix,
            priority=credential.priority,
            weight=credential.weight,
            base_url=None if credential.base_url is None else str(credential.base_url),
            headers=dict(credential.headers),
            proxy_url=proxy_url,
        )

    async def import_vertex_credential(
        self, record: EnvironmentRecord, filename: str, content: bytes, location: str
    ) -> None:
        await self._client.import_vertex_credential(record, filename, content, location)

    async def data_plane_health_check(self, record: EnvironmentRecord) -> bool:
        return await self._client.data_plane_health_check(record)

    async def apply_configuration(self, record: EnvironmentRecord, configuration: EnvironmentConfiguration) -> None:
        await self._client.apply_configuration(record, self.supplier(record.supplier), configuration)

    async def apply_global_settings(self, record: EnvironmentRecord, settings: AccountPoolSettings) -> None:
        await self._settings_sync.apply_global_settings(record, settings)

    async def apply_policy(self, record: EnvironmentRecord, policy: AccountPolicy) -> None:
        await self._settings_sync.apply_policy(record, policy)

    async def upload_auth_file(
        self, record: EnvironmentRecord, filename: str, content: bytes, content_type: str | None
    ) -> None:
        await self._client.upload_auth_file(record, filename, content, content_type)

    async def download_auth_file(self, record: EnvironmentRecord, filename: str) -> tuple[bytes, str]:
        return await self._client.download_auth_file(record, filename)

    async def delete_auth_file(self, record: EnvironmentRecord, filename: str) -> None:
        await self._client.delete_auth_file(record, filename)

    async def patch_auth_file_status(
        self, record: EnvironmentRecord, filename: str, auth_index: str | None, disabled: bool
    ) -> None:
        await self._client.patch_auth_file_status(record, filename, auth_index, disabled)

    async def patch_auth_file_fields(
        self, record: EnvironmentRecord, filename: str, fields: Mapping[str, object]
    ) -> None:
        await self._client.patch_auth_file_fields(record, filename, fields)

    async def get_auth_file_models(self, record: EnvironmentRecord, filename: str) -> tuple[str, ...]:
        return await self._client.get_auth_file_models(record, filename)

    async def list_plugins(self, record: EnvironmentRecord) -> Mapping[str, object]:
        return await self._client.list_plugins(record)

    async def list_plugin_store(self, record: EnvironmentRecord) -> Mapping[str, object]:
        return await self._client.list_plugin_store(record)

    async def install_plugin(
        self, record: EnvironmentRecord, plugin_id: str, version: str, source: str | None
    ) -> Mapping[str, object]:
        return await self._client.install_plugin(record, plugin_id, version, source)

    async def set_plugin_enabled(
        self, record: EnvironmentRecord, plugin_id: str, enabled: bool
    ) -> Mapping[str, object]:
        return await self._client.set_plugin_enabled(record, plugin_id, enabled)

    async def uninstall_plugin(self, record: EnvironmentRecord, plugin_id: str) -> Mapping[str, object]:
        return await self._client.uninstall_plugin(record, plugin_id)

    async def get_plugin_config(self, record: EnvironmentRecord, plugin_id: str) -> Mapping[str, object]:
        return await self._client.get_plugin_config(record, plugin_id)

    async def put_plugin_config(
        self, record: EnvironmentRecord, plugin_id: str, config: Mapping[str, object]
    ) -> Mapping[str, object]:
        return await self._client.put_plugin_config(record, plugin_id, config)

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
