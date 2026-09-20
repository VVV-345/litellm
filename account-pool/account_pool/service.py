"""本模块编排环境创建、授权、配置更新和状态恢复，不直接处理 HTTP 细节。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Final, Literal
from uuid import UUID

from account_pool.application.environment_state import (
    _AutomaticCooldownState,
    _cooldown_active,
    _cooldown_elapsed,
)
from account_pool.application.environments.auth_files import EnvironmentAuthFiles
from account_pool.application.environments.authorization import EnvironmentAuthorization
from account_pool.application.environments.configuration import EnvironmentConfigurationOperations
from account_pool.application.environments.contracts import (
    DIRECT_CREDENTIAL_VALIDATION_INTERVAL_SECONDS as _DIRECT_CREDENTIAL_VALIDATION_INTERVAL_SECONDS,
)
from account_pool.application.environments.contracts import (
    DIRECT_CREDENTIAL_VALIDATION_TIMEOUT_SECONDS as _DIRECT_CREDENTIAL_VALIDATION_TIMEOUT_SECONDS,
)
from account_pool.application.environments.contracts import AuthorizationConflict as _AuthorizationConflict
from account_pool.application.environments.contracts import T as T
from account_pool.application.environments.deletion import EnvironmentDeletion
from account_pool.application.environments.plugins import EnvironmentPlugins
from account_pool.application.environments.provisioning import EnvironmentProvisioning
from account_pool.application.environments.settings_sync import EnvironmentSettingsSync
from account_pool.application.plugin_validation import (
    _plugin_store_approves as _plugin_store_approves,
)
from account_pool.application.profile_updates import (
    _ExplicitProfileUpdate,
)
from account_pool.channels.registry import ChannelRegistry, UnsupportedChannelError
from account_pool.clash import ClashProxyNode
from account_pool.config import Settings, validate_proxy_profile_url
from account_pool.credential_ownership import CredentialConflict, CredentialOwnership
from account_pool.domain import (
    AuthorizationInstructionFlow,
    AuthorizationView,
    ChannelKind,
    CleanupProgress,
    CreateDirectCredentialEnvironmentRequest,
    CreateEnvironmentRequest,
    CreateVertexEnvironmentRequest,
    EnvironmentConfiguration,
    EnvironmentRecord,
    EnvironmentStatus,
    EnvironmentView,
    GatewayEnvironment,
    OAuthCallback,
    OpenAICompatibleCredentialDeleteRequest,
    OpenAICompatibleCredentialRequest,
    ProxyMode,
    ProxyProfile,
    SettingsProfileBaselines,
    SupplierKind,
    UpdateEnvironmentRequest,
    configuration_from_record,
    to_view,
    utc_now,
)
from account_pool.error_logs import ErrorLogService, LogStage
from account_pool.policies import AccountPolicy, PolicyRepository, PolicyView
from account_pool.ports import (
    CLIProxyClient,
    EnvironmentChannel,
    EnvironmentRepository,
    EnvironmentRuntime,
    ProxyProfileRepository,
)
from account_pool.providers.usage.contracts import ProviderQuotaError
from account_pool.proxy_gateways import GatewayConfigurationView, GatewayDelayView, GatewayView, ProxyGatewayService
from account_pool.settings import AccountPoolSettings, AccountPoolSettingsRepository
from account_pool.shared.error_safety import safe_error
from account_pool.shared.result import Failure, FailureCode, Result, Success
from account_pool.shared.secrets import EnvironmentSecretDeriver

_safe_error: Final = safe_error


class EnvironmentService:
    def __init__(
        self,
        settings: Settings,
        repository: EnvironmentRepository,
        runtime: EnvironmentRuntime,
        cli_proxy: CLIProxyClient,
        proxy_profiles: ProxyProfileRepository,
        secrets: EnvironmentSecretDeriver,
        channels: ChannelRegistry | None = None,
        proxy_gateways: ProxyGatewayService | None = None,
        error_logs: ErrorLogService | None = None,
        global_settings: AccountPoolSettingsRepository | None = None,
        policies: PolicyRepository | None = None,
        ownership: CredentialOwnership | None = None,
        direct_credential_validation_timeout_seconds: float = _DIRECT_CREDENTIAL_VALIDATION_TIMEOUT_SECONDS,
        direct_credential_validation_interval_seconds: float = _DIRECT_CREDENTIAL_VALIDATION_INTERVAL_SECONDS,
    ) -> None:
        self._ownership: Final = ownership or CredentialOwnership()
        self._settings: Final = settings
        self._repository: Final = repository
        self._runtime: Final = runtime
        self._cli_proxy: Final = cli_proxy
        self._proxy_profiles: Final = proxy_profiles
        self._secrets: Final = secrets
        self._channels: Final = channels or ChannelRegistry.default(self._settings, self._secrets)
        self._proxy_gateways: Final = proxy_gateways or ProxyGatewayService.disabled(self._settings, proxy_profiles)
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._locks_guard: Final = asyncio.Lock()
        self._error_logs: Final = error_logs
        self._global_settings: Final = global_settings
        self._policies: Final = policies
        self._direct_credential_validation_timeout_seconds: Final = direct_credential_validation_timeout_seconds
        self._direct_credential_validation_interval_seconds: Final = direct_credential_validation_interval_seconds

        self._settings_sync_operations: Final = EnvironmentSettingsSync(
            _apply_and_persist_configuration=self._apply_and_persist_configuration,
            _cli_proxy=self._cli_proxy,
            _lock_for=self._lock_for,
            _ownership=self._ownership,
            _policies=self._policies,
            _proxy_profiles=self._proxy_profiles,
            _repository=self._repository,
            update_environment=self.update_environment,
        )
        self._plugins_operations: Final = EnvironmentPlugins(
            _cli_proxy=self._cli_proxy,
            _log_event=self._log_event,
            _repository=self._repository,
        )
        self._auth_files_operations: Final = EnvironmentAuthFiles(
            _apply_and_persist_configuration=self._apply_and_persist_configuration,
            _channel=self._channel,
            _cli_proxy=self._cli_proxy,
            _gateway_environment=self._gateway_environment,
            _lock_for=self._lock_for,
            _log_event=self._log_event,
            _ownership=self._ownership,
            _refresh_if_needed=self._refresh_if_needed,
            _repository=self._repository,
            _secrets=self._secrets,
            _wait_for_direct_credential=self._wait_for_direct_credential,
        )
        self._provisioning_operations: Final = EnvironmentProvisioning(
            _account_pool_settings=self._account_pool_settings,
            _apply_and_persist_configuration=self._apply_and_persist_configuration,
            _authorization_view=self._authorization_view,
            _callback_state=self._callback_state,
            _channel=self._channel,
            _channels=self._channels,
            _cli_proxy=self._cli_proxy,
            _default_proxy=self._default_proxy,
            _direct_credential_validation_interval_seconds=self._direct_credential_validation_interval_seconds,
            _direct_credential_validation_timeout_seconds=self._direct_credential_validation_timeout_seconds,
            _find_by_operation_id=self._find_by_operation_id,
            _lock_for=self._lock_for,
            _log_event=self._log_event,
            _ownership=self._ownership,
            _proxy_profiles=self._proxy_profiles,
            _repository=self._repository,
            _secrets=self._secrets,
            _settings=self._settings,
            authorize_environment=self.authorize_environment,
            upload_auth_file=self.upload_auth_file,
        )
        self._authorization_operations: Final = EnvironmentAuthorization(
            _apply_and_persist_configuration=self._apply_and_persist_configuration,
            _channel=self._channel,
            _channels=self._channels,
            _gateway_environment=self._gateway_environment,
            _lock_for=self._lock_for,
            _log_event=self._log_event,
            _ownership=self._ownership,
            _repository=self._repository,
            _secrets=self._secrets,
            _settings=self._settings,
            _start_authorization=self._start_authorization,
        )
        self._configuration_operations: Final = EnvironmentConfigurationOperations(
            _channel=self._channel,
            _lock_for=self._lock_for,
            _log_event=self._log_event,
            _ownership=self._ownership,
            _proxy_profiles=self._proxy_profiles,
            _refresh_if_needed=self._refresh_if_needed,
            _repository=self._repository,
            delete_environment=self.delete_environment,
        )
        self._deletion_operations: Final = EnvironmentDeletion(
            _channel=self._channel,
            _lock_for=self._lock_for,
            _log_event=self._log_event,
            _ownership=self._ownership,
            _persist_cleanup_progress=self._persist_cleanup_progress,
            _repository=self._repository,
        )

    async def _account_pool_settings(self) -> AccountPoolSettings:
        if self._global_settings is None:
            return AccountPoolSettings()
        return (await self._global_settings.get()).values

    async def _default_proxy(self, settings: AccountPoolSettings) -> Result[tuple[ProxyMode, str | None, str]]:
        if settings.default_proxy_profile_id is None:
            return Success((ProxyMode.DEFAULT_GATEWAY, None, ""))
        proxy_url: Final = await self._proxy_profiles.get_url(settings.default_proxy_profile_id)
        if proxy_url is None:
            return Failure(FailureCode.INVALID, "default proxy profile is unavailable")
        try:
            validated_url: Final = validate_proxy_profile_url(proxy_url)
        except ValueError:
            return Failure(FailureCode.INVALID, "default proxy profile URL is invalid")
        return Success((ProxyMode.PROFILE, settings.default_proxy_profile_id, validated_url))

    async def _log_event(
        self,
        record: EnvironmentRecord,
        stage: LogStage,
        error: Exception | None,
        *,
        retryable: bool = False,
    ) -> None:
        if self._error_logs is not None:
            await self._error_logs.record(record, stage, error, retryable=retryable)

    async def list_environments(self) -> tuple[EnvironmentView, ...]:
        records: Final = await self._repository.list()
        refreshed: Final = await asyncio.gather(*(self._refresh_if_needed(record) for record in records))
        return tuple(to_view(record) for record in refreshed)

    def _channel(self, record: EnvironmentRecord) -> EnvironmentChannel:
        try:
            definition: Final = self._channels.get(record.channel)
            definition.supplier(record.supplier)
            return self._channels.channel(record.channel)
        except (KeyError, UnsupportedChannelError) as error:
            raise UnsupportedChannelError(str(error)) from error

    async def get_environment(self, environment_id: UUID) -> Result[EnvironmentView]:
        record: Final = await self._repository.get(environment_id)
        if record is None:
            return Failure(FailureCode.NOT_FOUND, "environment not found")
        refreshed: Final = await self._refresh_if_needed(record)
        return Success(to_view(refreshed))

    async def refresh_environment(self, environment_id: UUID) -> Result[EnvironmentView]:
        record: Final = await self._repository.get(environment_id)
        if record is None:
            return Failure(FailureCode.NOT_FOUND, "environment not found")
        try:
            refreshed: Final = await self._refresh_if_needed(record, refresh_quota=True)
        except Exception:
            return Failure(FailureCode.UPSTREAM, "environment quota refresh failed")
        return Success(to_view(refreshed))

    async def refresh_ready_quotas(self, max_concurrency: int = 3) -> tuple[UUID, ...]:
        records: Final = await self._repository.list()
        ready: Final = tuple(record for record in records if record.status is EnvironmentStatus.READY)
        semaphore: Final = asyncio.Semaphore(max(1, max_concurrency))

        async def refresh(record: EnvironmentRecord) -> UUID | None:
            async with semaphore:
                result: Final = await self.refresh_environment(record.id)
                return (
                    record.id if isinstance(result, Failure) or result.value.quota.refresh_status == "failed" else None
                )

        failed: Final = await asyncio.gather(*(refresh(record) for record in ready))
        return tuple(card_id for card_id in failed if card_id is not None)

    async def refresh_auth_files(self) -> tuple[UUID, ...]:
        records: Final = await self._repository.list()
        auth_file_records: Final = tuple(record for record in records if record.auth_file_name is not None)
        results: Final = await asyncio.gather(
            *(
                self._refresh_if_needed(
                    record,
                    credential_state_changed=True,
                    raise_on_error=True,
                    wait_for_lock=True,
                )
                for record in auth_file_records
            ),
            return_exceptions=True,
        )
        return tuple(
            record.id
            for record, result in zip(auth_file_records, results)
            if isinstance(result, BaseException) or result.status is EnvironmentStatus.ERROR
        )

    async def list_proxy_profiles(self) -> tuple[ProxyProfile, ...]:
        return await self._proxy_profiles.list()

    async def sync_global_settings(
        self, settings: AccountPoolSettings, *, rollback_on_failure: bool = True
    ) -> tuple[UUID, ...]:
        return await self._settings_sync_operations.sync_global_settings(
            settings, rollback_on_failure=rollback_on_failure
        )

    async def _sync_global_settings_for_record(
        self,
        record: EnvironmentRecord,
        settings: AccountPoolSettings,
        update: _ExplicitProfileUpdate | None,
        policy: PolicyView | None,
    ) -> None:
        return await self._settings_sync_operations.sync_global_settings_for_record(record, settings, update, policy)

    def _explicit_profile_update(
        self, record: EnvironmentRecord, settings: AccountPoolSettings
    ) -> _ExplicitProfileUpdate | None:
        return self._settings_sync_operations.explicit_profile_update(record, settings)

    async def _apply_explicit_profile_configuration(
        self, record: EnvironmentRecord, update: _ExplicitProfileUpdate | None
    ) -> EnvironmentRecord:
        return await self._settings_sync_operations.apply_explicit_profile_configuration(record, update)

    async def _restore_settings_configuration(self, snapshot: EnvironmentRecord, operation_id: str) -> None:
        return await self._settings_sync_operations.restore_settings_configuration(snapshot, operation_id)

    async def _configuration_for_snapshot(self, snapshot: EnvironmentRecord) -> EnvironmentConfiguration:
        return await self._settings_sync_operations.configuration_for_snapshot(snapshot)

    async def _proxy_url_for_snapshot(self, snapshot: EnvironmentRecord) -> str:
        return await self._settings_sync_operations.proxy_url_for_snapshot(snapshot)

    async def _set_policy_runtime_status(
        self,
        policy: PolicyView,
        status: Literal["synced", "failed"],
        error: str | None = None,
        *,
        require_current: bool = True,
    ) -> None:
        return await self._settings_sync_operations.set_policy_runtime_status(
            policy, status, error, require_current=require_current
        )

    async def sync_policy(self, record: EnvironmentRecord, policy: AccountPolicy) -> None:
        return await self._settings_sync_operations.sync_policy(record, policy)

    async def list_card_plugins(self, environment_id: UUID) -> Result[Mapping[str, object]]:
        return await self._plugins_operations.list_card_plugins(environment_id)

    async def list_card_plugin_store(self, environment_id: UUID) -> Result[Mapping[str, object]]:
        return await self._plugins_operations.list_card_plugin_store(environment_id)

    async def install_card_plugin(
        self, environment_id: UUID, plugin_id: str, version: str, source: str | None
    ) -> Result[Mapping[str, object]]:
        return await self._plugins_operations.install_card_plugin(environment_id, plugin_id, version, source)

    async def set_card_plugin_enabled(
        self, environment_id: UUID, plugin_id: str, enabled: bool
    ) -> Result[Mapping[str, object]]:
        return await self._plugins_operations.set_card_plugin_enabled(environment_id, plugin_id, enabled)

    async def uninstall_card_plugin(self, environment_id: UUID, plugin_id: str) -> Result[Mapping[str, object]]:
        return await self._plugins_operations.uninstall_card_plugin(environment_id, plugin_id)

    async def get_card_plugin_config(self, environment_id: UUID, plugin_id: str) -> Result[Mapping[str, object]]:
        return await self._plugins_operations.get_card_plugin_config(environment_id, plugin_id)

    async def put_card_plugin_config(
        self, environment_id: UUID, plugin_id: str, config: Mapping[str, object]
    ) -> Result[Mapping[str, object]]:
        return await self._plugins_operations.put_card_plugin_config(environment_id, plugin_id, config)

    async def _plugin_call(
        self, environment_id: UUID, operation: Callable[[EnvironmentRecord], Awaitable[Mapping[str, object]]]
    ) -> Result[Mapping[str, object]]:
        return await self._plugins_operations.plugin_call(environment_id, operation)

    async def upload_auth_file(
        self, environment_id: UUID, filename: str, content: bytes, content_type: str | None, *, replace: bool = False
    ) -> Result[EnvironmentView]:
        return await self._auth_files_operations.upload_auth_file(
            environment_id, filename, content, content_type, replace=replace
        )

    async def _upload_auth_file_locked(
        self,
        record: EnvironmentRecord,
        filename: str,
        content: bytes,
        content_type: str | None,
        pending_state: str | None,
    ) -> Result[EnvironmentView]:
        return await self._auth_files_operations.upload_auth_file_locked(
            record, filename, content, content_type, pending_state
        )

    async def download_auth_file(self, environment_id: UUID) -> Result[tuple[bytes, str, str]]:
        return await self._auth_files_operations.download_auth_file(environment_id)

    async def delete_auth_file(self, environment_id: UUID) -> Result[EnvironmentView]:
        return await self._auth_files_operations.delete_auth_file(environment_id)

    async def patch_auth_file_status(self, environment_id: UUID, disabled: bool) -> Result[EnvironmentView]:
        return await self._auth_files_operations.patch_auth_file_status(environment_id, disabled)

    async def patch_auth_file_fields(
        self, environment_id: UUID, fields: Mapping[str, object]
    ) -> Result[EnvironmentView]:
        return await self._auth_files_operations.patch_auth_file_fields(environment_id, fields)

    async def get_auth_file_models(self, environment_id: UUID) -> Result[tuple[str, ...]]:
        return await self._auth_files_operations.get_auth_file_models(environment_id)

    async def list_proxy_gateways(self) -> tuple[GatewayView, ...]:
        return await self._proxy_gateways.list_gateways()

    async def measure_proxy_gateway_delays(self) -> tuple[GatewayDelayView, ...]:
        return await self._proxy_gateways.measure_delays()

    def proxy_gateway_configuration(self) -> GatewayConfigurationView:
        return self._proxy_gateways.configuration()

    async def list_clash_nodes(self) -> tuple[ClashProxyNode, ...]:
        return await self._proxy_gateways.list_nodes()

    async def switch_proxy_gateway(self, port: int, node_name: str) -> GatewayView:
        view: Final = await self._proxy_gateways.switch_gateway(port, node_name)
        await self._proxy_gateways.sync_profiles()
        return view

    async def add_proxy_gateway(self) -> GatewayView:
        return await self._proxy_gateways.add_gateway()

    async def remove_proxy_gateway(self, port: int) -> None:
        records: Final = await self._repository.list()
        referenced: Final = frozenset(
            record.proxy_profile_id for record in records if record.proxy_profile_id is not None
        )
        await self._proxy_gateways.remove_gateway(port, referenced)

    async def list_gateway_environments(self) -> tuple[GatewayEnvironment, ...]:
        records: Final = await self._repository.list()
        refreshed: Final = await asyncio.gather(*(self._refresh_if_needed(record) for record in records))
        policies: Final = () if self._policies is None else await self._policies.list()
        by_card: Final = {entry.card_id: entry.policy for entry in policies}
        return tuple(
            endpoint.model_copy(
                update={
                    "quota": record.quota,
                    "model_quotas": record.model_quotas,
                    "model_cooldowns": record.model_cooldowns,
                    "model_aliases": {alias.alias: alias.target for alias in policy.model_aliases},
                    "quota_reserve_percent": policy.routing.quota_reserve_percent,
                    "quota_snapshot_max_age": policy.routing.quota_snapshot_max_age,
                    "routing_weight": policy.routing.weight,
                    "routing_order": (20001 if policy.routing.is_backup else 0) - policy.routing.priority,
                    "public_models": tuple(
                        dict.fromkeys(
                            model
                            for model in (
                                *endpoint.enabled_models,
                                *(
                                    alias.alias
                                    for alias in policy.model_aliases
                                    if alias.target in endpoint.enabled_models
                                    and alias.target not in policy.excluded_models
                                ),
                            )
                            if model not in policy.excluded_models
                        )
                    ),
                }
            )
            for record in refreshed
            for endpoint in (self._gateway_environment(record),)
            for policy in (by_card.get(record.id, AccountPolicy()),)
        )

    def gateway_environment(self, record: EnvironmentRecord) -> GatewayEnvironment:
        return self._gateway_environment(record)

    async def _start_authorization(
        self, record: EnvironmentRecord
    ) -> tuple[str, str, str, AuthorizationInstructionFlow, str | None, int | None]:
        return await self._provisioning_operations.start_authorization(record)

    async def create_environment(
        self, request: CreateEnvironmentRequest, *, environment_id: UUID | None = None
    ) -> Result[AuthorizationView]:
        return await self._provisioning_operations.create_environment(request, environment_id=environment_id)

    async def create_direct_credential_environment(
        self, request: CreateDirectCredentialEnvironmentRequest
    ) -> Result[EnvironmentView]:
        return await self._provisioning_operations.create_direct_credential_environment(request)

    async def create_auth_file_environment(
        self, request: CreateEnvironmentRequest, environment_id: UUID, filename: str, content: bytes
    ) -> Result[EnvironmentView]:
        return await self._provisioning_operations.create_auth_file_environment(
            request, environment_id, filename, content
        )

    async def pending_authorization(self, environment_id: UUID) -> Result[AuthorizationView]:
        return await self._provisioning_operations.pending_authorization(environment_id)

    async def resume_onboarding_oauth(self, environment_id: UUID) -> Result[AuthorizationView]:
        return await self._provisioning_operations.resume_onboarding_oauth(environment_id)

    async def create_vertex_environment(
        self, request: CreateVertexEnvironmentRequest, filename: str, content: bytes
    ) -> Result[EnvironmentView]:
        return await self._provisioning_operations.create_vertex_environment(request, filename, content)

    async def _create_direct_credential_environment(
        self,
        *,
        name: str,
        supplier: SupplierKind,
        operation_id: str | None,
        credential_content: bytes,
        write_credential: Callable[[EnvironmentChannel, EnvironmentRecord, str], Awaitable[None]],
        auth_file: bool = False,
        environment_id: UUID | None = None,
    ) -> Result[EnvironmentView]:
        return await self._provisioning_operations.provision_direct_credential_environment(
            name=name,
            supplier=supplier,
            operation_id=operation_id,
            credential_content=credential_content,
            write_credential=write_credential,
            auth_file=auth_file,
            environment_id=environment_id,
        )

    async def _wait_for_direct_credential(
        self, channel: EnvironmentChannel, record: EnvironmentRecord
    ) -> EnvironmentRecord:
        return await self._provisioning_operations.wait_for_direct_credential(channel, record)

    async def create_openai_compatible(self, request: CreateEnvironmentRequest) -> Result[EnvironmentView]:
        return await self._provisioning_operations.create_openai_compatible(request)

    async def add_openai_compatible_credential(
        self, environment_id: UUID, request: OpenAICompatibleCredentialRequest
    ) -> Result[EnvironmentView]:
        return await self._provisioning_operations.add_openai_compatible_credential(environment_id, request)

    async def delete_openai_compatible_credential(
        self, environment_id: UUID, request: OpenAICompatibleCredentialDeleteRequest
    ) -> Result[EnvironmentView]:
        return await self._provisioning_operations.delete_openai_compatible_credential(environment_id, request)

    async def authorize_environment(
        self, environment_id: UUID, operation_id: str | None = None
    ) -> Result[AuthorizationView]:
        return await self._authorization_operations.authorize_environment(environment_id, operation_id)

    async def _authorize_prepared(
        self, record: EnvironmentRecord, operation_id: str | None
    ) -> Result[AuthorizationView]:
        return await self._authorization_operations.authorize_prepared(record, operation_id)

    async def cancel_oauth_session(self, environment_id: UUID) -> Result[EnvironmentView]:
        return await self._authorization_operations.cancel_oauth_session(environment_id)

    async def submit_oauth_callback(
        self, callback: OAuthCallback, environment_id: UUID | None = None
    ) -> Result[EnvironmentView]:
        return await self._authorization_operations.submit_oauth_callback(callback, environment_id)

    async def _submit_oauth_callback_locked(
        self, callback: OAuthCallback, environment_id: UUID | None, record: EnvironmentRecord
    ) -> Result[EnvironmentView]:
        return await self._authorization_operations.submit_oauth_callback_locked(callback, environment_id, record)

    async def _find_by_operation_id(self, operation_id: str) -> EnvironmentRecord | None:
        return await self._authorization_operations.find_by_operation_id(operation_id)

    async def _consume_oauth_state(self, state: str, consumed_at: datetime) -> EnvironmentRecord | None:
        return await self._authorization_operations.consume_oauth_state(state, consumed_at)

    async def _persist_authorization_failure(self, record: EnvironmentRecord, message: str) -> EnvironmentRecord:
        return await self._authorization_operations.persist_authorization_failure(record, message)

    def _state_signature(self, environment_id: UUID, state: str) -> str:
        return self._authorization_operations.state_signature(environment_id, state)

    def _callback_state(self, record: EnvironmentRecord) -> str:
        return self._authorization_operations.callback_state(record)

    def _valid_state_signature(self, record: EnvironmentRecord, state: str) -> bool:
        return self._authorization_operations.valid_state_signature(record, state)

    def _authorization_view(self, record: EnvironmentRecord) -> AuthorizationView:
        return self._authorization_operations.authorization_view(record)

    async def _validate_authorized(self, record: EnvironmentRecord) -> EnvironmentRecord:
        return await self._authorization_operations.validate_authorized(record)

    async def _persist_validation_retry(self, record: EnvironmentRecord, message: str) -> EnvironmentRecord:
        return await self._authorization_operations.persist_validation_retry(record, message)

    async def _complete_authorization(self, record: EnvironmentRecord) -> Result[EnvironmentRecord]:
        return await self._authorization_operations.complete_authorization(record)

    async def _persist_cleanup_progress(
        self, record: EnvironmentRecord, progress: CleanupProgress
    ) -> EnvironmentRecord | None:
        return await self._authorization_operations.persist_cleanup_progress(record, progress)

    async def update_environment(
        self,
        environment_id: UUID,
        request: UpdateEnvironmentRequest,
        *,
        settings_profile_baselines: SettingsProfileBaselines | Literal["preserve"] = "preserve",
    ) -> Result[EnvironmentView]:
        return await self._configuration_operations.update_environment(
            environment_id, request, settings_profile_baselines=settings_profile_baselines
        )

    async def reconcile_pending_configurations(self) -> tuple[EnvironmentView, ...]:
        return await self._configuration_operations.reconcile_pending_configurations()

    async def reconcile_pending_authorizations(self) -> None:
        return await self._configuration_operations.reconcile_pending_authorizations()

    async def reconcile_pending_deletions(self) -> None:
        return await self._configuration_operations.reconcile_pending_deletions()

    async def _reconcile_configuration(self, record: EnvironmentRecord) -> Result[EnvironmentView]:
        return await self._configuration_operations.reconcile_configuration(record)

    async def _apply_and_persist_configuration(
        self, record: EnvironmentRecord, desired: EnvironmentConfiguration
    ) -> Result[EnvironmentView]:
        return await self._configuration_operations.apply_and_persist_configuration(record, desired)

    async def delete_environment(self, environment_id: UUID, operation_id: str | None = None) -> Result[None]:
        return await self._deletion_operations.delete_environment(environment_id, operation_id)

    async def _remove_compose_step(self, record: EnvironmentRecord) -> EnvironmentRecord | None:
        return await self._deletion_operations.remove_compose_step(record)

    async def _remove_directory_step(self, record: EnvironmentRecord) -> EnvironmentRecord | None:
        return await self._deletion_operations.remove_directory_step(record)

    async def _automatic_cooldown_before_update(
        self, record: EnvironmentRecord, manual_cooldown: bool
    ) -> _AutomaticCooldownState:
        return await self._configuration_operations.automatic_cooldown_before_update(record, manual_cooldown)

    async def _data_plane_health_check(self, record: EnvironmentRecord) -> bool:
        return await self._configuration_operations.data_plane_health_check(record)

    async def _refresh_if_needed(
        self,
        record: EnvironmentRecord,
        *,
        refresh_quota: bool = False,
        credential_state_changed: bool = False,
        raise_on_error: bool = False,
        wait_for_lock: bool = False,
    ) -> EnvironmentRecord:
        if record.status not in (
            EnvironmentStatus.AWAITING_AUTHORIZATION,
            EnvironmentStatus.VALIDATING,
            EnvironmentStatus.READY,
            EnvironmentStatus.COOLING_DOWN,
            EnvironmentStatus.DISABLED,
            EnvironmentStatus.ERROR,
        ):
            return record
        if record.status is EnvironmentStatus.ERROR and not credential_state_changed:
            return record
        lock: Final = await self._lock_for(record.id)
        if lock.locked() and not wait_for_lock:
            return record
        async with lock, self._ownership.operation(record.id):
            current: Final = await self._repository.get(record.id)
            if current is None or current.status is EnvironmentStatus.DELETING:
                return current or record
            if (
                current.configuration_pending
                or current.desired_configuration_version > current.observed_configuration_version
            ):
                desired: Final = current.desired_configuration or configuration_from_record(current)
                await self._apply_and_persist_configuration(current, desired)
                # 配置写入失败后必须重新读取条件持久化结果，避免旧 ready 快照掩盖不可路由状态。
                durable: Final = await self._repository.get(record.id)
                return durable or current
            if current.status == EnvironmentStatus.AWAITING_AUTHORIZATION:
                return await self._refresh_authorization(current)
            if current.status == EnvironmentStatus.VALIDATING:
                try:
                    completion: Final = await self._complete_authorization(current)
                except _AuthorizationConflict:
                    return await self._repository.get(current.id) or current
                if isinstance(completion, Failure):
                    return await self._repository.get(current.id) or current
                return completion.value
            if current.auth_file_name is None and current.channel is not ChannelKind.OPENAI_COMPATIBLE:
                return current
            channel: Final = self._channel(current)
            if not credential_state_changed:
                if current.automatic_cooldown and not await channel.data_plane_health_check(current):
                    return current
                if _cooldown_active(current):
                    return current
                if _cooldown_elapsed(current) and not await channel.data_plane_health_check(current):
                    return current
            try:
                observed: Final = await channel.read_account(current, refresh_quota=refresh_quota)
            except CredentialConflict as error:
                blocked: Final = current.model_copy(
                    update={
                        "version": current.version + 1,
                        "status": EnvironmentStatus.ERROR,
                        "desired_state": EnvironmentStatus.ERROR,
                        "credential_fingerprints": (),
                        "last_error": str(error),
                        "updated_at": utc_now(),
                    }
                )
                saved_blocked: Final = await self._repository.save_if_version(blocked, current.version)
                return saved_blocked or current
            except Exception as error:
                if isinstance(error, ProviderQuotaError) and error.failures:
                    for failure in error.failures:
                        await self._log_event(
                            current,
                            "quota",
                            ProviderQuotaError((failure,), "Provider quota refresh failed"),
                        )
                else:
                    await self._log_event(current, "quota", error)
                if refresh_quota or raise_on_error:
                    raise
                return current
            refreshed: Final = observed.model_copy(
                update={
                    "version": current.version,
                    "desired_state": current.desired_state,
                    "operation_id": current.operation_id,
                    "desired_configuration_version": current.desired_configuration_version,
                    "observed_configuration_version": current.observed_configuration_version,
                    "desired_configuration": current.desired_configuration,
                    "configuration_pending": current.configuration_pending,
                    "configuration_last_error": None,
                    "updated_at": utc_now(),
                }
            )
            refresh_failures: Final = refreshed.quota.refresh_failures
            saved: Final = await self._repository.save(refreshed)
            if refresh_quota and saved.quota.refresh_status in ("partial", "failed") and saved.quota.refresh_error:
                if refresh_failures:
                    for failure in refresh_failures:
                        await self._log_event(
                            saved,
                            "quota",
                            ProviderQuotaError((failure,), "Provider quota refresh partially failed"),
                        )
                else:
                    await self._log_event(saved, "quota", RuntimeError(saved.quota.refresh_error), retryable=True)
            elif refresh_quota and saved.quota.refresh_status == "complete":
                await self._log_event(saved, "quota", None)
            return saved

    async def _reloaded_consumed_state(self, record: EnvironmentRecord, state: str) -> EnvironmentRecord | None:
        return await self._authorization_operations.reloaded_consumed_state(record, state)

    async def _update_authorization_error(self, record: EnvironmentRecord, message: str | None) -> EnvironmentRecord:
        return await self._authorization_operations.update_authorization_error(record, message)

    async def _refresh_authorization(self, record: EnvironmentRecord) -> EnvironmentRecord:
        return await self._authorization_operations.refresh_authorization(record)

    async def _resolve_proxy(self, request: UpdateEnvironmentRequest) -> Result[str]:
        return await self._configuration_operations.resolve_proxy(request)

    def _gateway_environment(self, record: EnvironmentRecord) -> GatewayEnvironment:
        return self._channel(record).gateway(record)

    async def _lock_for(self, environment_id: UUID) -> asyncio.Lock:
        async with self._locks_guard:
            existing: Final = self._locks.get(environment_id)
            if existing is not None:
                return existing
            created: Final = asyncio.Lock()
            self._locks[environment_id] = created
            return created
