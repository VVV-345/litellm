"""本模块编排环境创建、授权、配置更新和状态恢复，不直接处理 HTTP 细节。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets as token_secrets
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, TypeVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

from pydantic import HttpUrl, TypeAdapter

from account_pool.channels.registry import ChannelRegistry, UnsupportedChannelError
from account_pool.clash import ClashProxyNode
from account_pool.cleanup import compose_removed, directory_removed, routes_removed
from account_pool.config import Settings, validate_proxy_profile_url
from account_pool.domain import (
    AuthorizationFlow,
    AuthorizationView,
    ChannelKind,
    CleanupProgress,
    CreateEnvironmentRequest,
    EnvironmentConfiguration,
    EnvironmentRecord,
    EnvironmentStatus,
    EnvironmentView,
    GatewayEnvironment,
    OAuthCallback,
    OpenAICompatibleConfiguration,
    OpenAICompatibleCredential,
    OpenAICompatibleCredentialDeleteRequest,
    OpenAICompatibleCredentialRequest,
    Provider,
    ProxyMode,
    ProxyProfile,
    QuotaSnapshot,
    SupplierKind,
    UpdateEnvironmentRequest,
    configuration_from_record,
    to_view,
    utc_now,
)
from account_pool.error_logs import ErrorLogService, LogStage
from account_pool.error_safety import safe_error
from account_pool.policies import AccountPolicy
from account_pool.ports import (
    CLIProxyClient,
    EnvironmentChannel,
    EnvironmentRepository,
    EnvironmentRuntime,
    ProxyProfileRepository,
)
from account_pool.proxy_gateways import GatewayConfigurationView, GatewayDelayView, GatewayView, ProxyGatewayService
from account_pool.result import Failure, FailureCode, Result, Success
from account_pool.secrets import EnvironmentSecretDeriver, SecretPurpose, StateCipher
from account_pool.settings import AccountPoolSettings, AccountPoolSettingsRepository

T = TypeVar("T")
_HTTP_URL_ADAPTER: Final = TypeAdapter(HttpUrl)


async def _constant_async(value: T) -> T:
    return value


class _AuthorizationConflict(Exception):
    """授权回调未能持久化到可路由状态时阻止成功响应。"""


class _AutomaticCooldownState(StrEnum):
    NONE = "none"
    ACTIVE = "active"
    RECOVERED = "recovered"
    BLOCKED = "blocked"


# 授权完成后允许保留用户主动停用或冷却状态，不能把有效凭据误判为验证失败。
_AUTHORIZATION_COMPLETE_STATUSES: Final = frozenset(
    (EnvironmentStatus.READY, EnvironmentStatus.DISABLED, EnvironmentStatus.COOLING_DOWN)
)
_AUTHORIZATION_VALIDATION_TIMEOUT: Final = timedelta(minutes=2)


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
    ) -> None:
        self._settings: Final = settings
        self._repository: Final = repository
        self._runtime: Final = runtime
        self._cli_proxy: Final = cli_proxy
        self._proxy_profiles: Final = proxy_profiles
        self._secrets: Final = secrets
        self._channels: Final = channels or ChannelRegistry.default(self._settings, self._secrets)
        self._proxy_gateways: Final = proxy_gateways or ProxyGatewayService.disabled(
            self._settings, proxy_profiles
        )
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._locks_guard: Final = asyncio.Lock()
        self._error_logs: Final = error_logs
        self._global_settings: Final = global_settings

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
        self, record: EnvironmentRecord, stage: LogStage, error: Exception | None, *, retryable: bool = False,
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
        refreshed: Final = await self._refresh_if_needed(record)
        return Success(to_view(refreshed))

    async def list_proxy_profiles(self) -> tuple[ProxyProfile, ...]:
        return await self._proxy_profiles.list()

    async def sync_global_settings(self, settings: AccountPoolSettings) -> tuple[UUID, ...]:
        records: Final = await self._repository.list()
        results: Final = await asyncio.gather(
            *(self._sync_global_settings_for_record(record, settings) for record in records),
            return_exceptions=True,
        )
        return tuple(record.id for record, result in zip(records, results) if isinstance(result, Exception))

    async def _sync_global_settings_for_record(
        self, record: EnvironmentRecord, settings: AccountPoolSettings
    ) -> None:
        if record.channel is not ChannelKind.CLIPROXYAPI or record.status is EnvironmentStatus.DELETING:
            return
        await self._cli_proxy.apply_global_settings(record, settings)

    async def sync_policy(self, record: EnvironmentRecord, policy: AccountPolicy) -> None:
        if record.channel is ChannelKind.CLIPROXYAPI and record.status is not EnvironmentStatus.DELETING:
            await self._cli_proxy.apply_policy(record, policy)

    async def list_card_plugins(self, environment_id: UUID) -> Result[Mapping[str, object]]:
        return await self._plugin_call(environment_id, lambda record: self._cli_proxy.list_plugins(record))

    async def list_card_plugin_store(self, environment_id: UUID) -> Result[Mapping[str, object]]:
        return await self._plugin_call(environment_id, lambda record: self._cli_proxy.list_plugin_store(record))

    async def install_card_plugin(
        self, environment_id: UUID, plugin_id: str, version: str
    ) -> Result[Mapping[str, object]]:
        return await self._plugin_call(
            environment_id, lambda record: self._cli_proxy.install_plugin(record, plugin_id, version)
        )

    async def set_card_plugin_enabled(
        self, environment_id: UUID, plugin_id: str, enabled: bool
    ) -> Result[Mapping[str, object]]:
        return await self._plugin_call(
            environment_id, lambda record: self._cli_proxy.set_plugin_enabled(record, plugin_id, enabled)
        )

    async def uninstall_card_plugin(self, environment_id: UUID, plugin_id: str) -> Result[Mapping[str, object]]:
        return await self._plugin_call(
            environment_id, lambda record: self._cli_proxy.uninstall_plugin(record, plugin_id)
        )

    async def get_card_plugin_config(self, environment_id: UUID, plugin_id: str) -> Result[Mapping[str, object]]:
        return await self._plugin_call(
            environment_id, lambda record: self._cli_proxy.get_plugin_config(record, plugin_id)
        )

    async def put_card_plugin_config(
        self, environment_id: UUID, plugin_id: str, config: Mapping[str, object]
    ) -> Result[Mapping[str, object]]:
        return await self._plugin_call(
            environment_id, lambda record: self._cli_proxy.put_plugin_config(record, plugin_id, config)
        )

    async def _plugin_call(
        self, environment_id: UUID, operation: Callable[[EnvironmentRecord], Awaitable[Mapping[str, object]]]
    ) -> Result[Mapping[str, object]]:
        record: Final = await self._repository.get(environment_id)
        if record is None:
            return Failure(FailureCode.NOT_FOUND, "environment not found")
        if record.channel is not ChannelKind.CLIPROXYAPI:
            return Failure(FailureCode.INVALID, "plugins are supported by CLIProxyAPI cards only")
        try:
            return Success(await operation(record))
        except Exception as error:
            await self._log_event(record, "configuration", error)
            return Failure(FailureCode.UPSTREAM, "plugin runtime operation failed")

    async def upload_auth_file(
        self, environment_id: UUID, filename: str, content: bytes, content_type: str | None
    ) -> Result[EnvironmentView]:
        record: Final = await self._repository.get(environment_id)
        if record is None:
            return Failure(FailureCode.NOT_FOUND, "environment not found")
        if record.channel is not ChannelKind.CLIPROXYAPI:
            return Failure(FailureCode.INVALID, "auth files are supported by CLIProxyAPI cards only")
        try:
            await self._cli_proxy.upload_auth_file(record, filename, content, content_type)
        except Exception as error:
            await self._log_event(record, "authentication", error)
            return Failure(FailureCode.UPSTREAM, "auth file upload failed")
        return await self.refresh_environment(environment_id)

    async def download_auth_file(self, environment_id: UUID) -> Result[tuple[bytes, str, str]]:
        record: Final = await self._repository.get(environment_id)
        if record is None:
            return Failure(FailureCode.NOT_FOUND, "environment not found")
        if record.channel is not ChannelKind.CLIPROXYAPI or record.auth_file_name is None:
            return Failure(FailureCode.INVALID, "this card has no downloadable auth file")
        try:
            content, content_type = await self._cli_proxy.download_auth_file(record, record.auth_file_name)
        except Exception as error:
            await self._log_event(record, "authentication", error)
            return Failure(FailureCode.UPSTREAM, "auth file download failed")
        return Success((content, content_type, record.auth_file_name))

    async def delete_auth_file(self, environment_id: UUID) -> Result[EnvironmentView]:
        record: Final = await self._repository.get(environment_id)
        if record is None:
            return Failure(FailureCode.NOT_FOUND, "environment not found")
        if record.channel is not ChannelKind.CLIPROXYAPI or record.auth_file_name is None:
            return Failure(FailureCode.INVALID, "this card has no auth file")
        try:
            await self._cli_proxy.delete_auth_file(record, record.auth_file_name)
        except Exception as error:
            await self._log_event(record, "authentication", error)
            return Failure(FailureCode.UPSTREAM, "auth file deletion failed")
        cleared: Final = record.model_copy(
            update={
                "version": record.version + 1,
                "status": EnvironmentStatus.AWAITING_AUTHORIZATION,
                "desired_state": EnvironmentStatus.AWAITING_AUTHORIZATION,
                "configuration_pending": False,
                "auth_file_name": None,
                "auth_index": None,
                "available_models": (),
                "enabled_models": (),
                "quota": QuotaSnapshot(),
                "model_quotas": (),
                "cooldown_until": None,
                "automatic_cooldown": False,
                "updated_at": utc_now(),
            }
        )
        await self._repository.save_if_version(cleared, record.version)
        return Success(to_view(cleared))

    async def patch_auth_file_status(
        self, environment_id: UUID, disabled: bool
    ) -> Result[EnvironmentView]:
        record: Final = await self._repository.get(environment_id)
        if record is None:
            return Failure(FailureCode.NOT_FOUND, "environment not found")
        if record.channel is not ChannelKind.CLIPROXYAPI or record.auth_file_name is None:
            return Failure(FailureCode.INVALID, "this card has no auth file")
        try:
            await self._cli_proxy.patch_auth_file_status(record, record.auth_file_name, record.auth_index, disabled)
        except Exception as error:
            await self._log_event(record, "authentication", error)
            return Failure(FailureCode.UPSTREAM, "auth file status update failed")
        return await self.refresh_environment(environment_id)

    async def patch_auth_file_fields(
        self, environment_id: UUID, fields: Mapping[str, object]
    ) -> Result[EnvironmentView]:
        record: Final = await self._repository.get(environment_id)
        if record is None:
            return Failure(FailureCode.NOT_FOUND, "environment not found")
        if record.channel is not ChannelKind.CLIPROXYAPI or record.auth_file_name is None:
            return Failure(FailureCode.INVALID, "this card has no auth file")
        if not fields:
            return Failure(FailureCode.INVALID, "auth file fields are required")
        try:
            await self._cli_proxy.patch_auth_file_fields(record, record.auth_file_name, fields)
        except Exception as error:
            await self._log_event(record, "authentication", error)
            return Failure(FailureCode.UPSTREAM, "auth file fields update failed")
        return await self.refresh_environment(environment_id)

    async def get_auth_file_models(self, environment_id: UUID) -> Result[tuple[str, ...]]:
        record: Final = await self._repository.get(environment_id)
        if record is None:
            return Failure(FailureCode.NOT_FOUND, "environment not found")
        if record.channel is not ChannelKind.CLIPROXYAPI or record.auth_file_name is None:
            return Failure(FailureCode.INVALID, "this card has no auth file")
        try:
            return Success(await self._cli_proxy.get_auth_file_models(record, record.auth_file_name))
        except Exception as error:
            await self._log_event(record, "authentication", error)
            return Failure(FailureCode.UPSTREAM, "auth file model query failed")

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

    async def list_gateway_environments(self) -> tuple[GatewayEnvironment, ...]:
        records: Final = await self._repository.list()
        refreshed: Final = await asyncio.gather(*(self._refresh_if_needed(record) for record in records))
        return tuple(self._gateway_environment(record) for record in refreshed)

    def gateway_environment(self, record: EnvironmentRecord) -> GatewayEnvironment:
        return self._gateway_environment(record)

    async def _start_authorization(
        self,
        record: EnvironmentRecord,
    ) -> tuple[str, str, str, AuthorizationFlow, str | None, int | None]:
        channel: Final = self._channel(record)
        result: Final = await channel.start_authorization(record)
        supplier: Final = channel.supplier(record.supplier)
        callback_state: Final = self._callback_state(record)
        callback_url: Final = _replace_state(result.authorization_url, callback_state)
        return (
            result.provider_state,
            callback_state,
            callback_url,
            supplier.authorization_flow,
            result.user_code,
            result.expires_in_seconds,
        )

    async def create_environment(self, request: CreateEnvironmentRequest) -> Result[AuthorizationView]:
        if request.channel is ChannelKind.FREEBUFF2API:
            return Failure(FailureCode.INVALID, "freebuff2api channel has been retired; create an OpenAI-compatible card")
        try:
            channel_definition: Final = self._channels.get(request.channel)
            supplier_definition: Final = channel_definition.supplier(request.supplier)
        except (KeyError, UnsupportedChannelError) as error:
            return Failure(FailureCode.INVALID, str(error))
        if request.operation_id is not None:
            existing: Final = await self._find_by_operation_id(request.operation_id)
            if existing is not None:
                if existing.oauth_authorization_url is not None and existing.oauth_expires_at is not None:
                    return Success(self._authorization_view(existing))
                return Failure(FailureCode.CONFLICT, "environment operation is still in progress")
        pool_settings: Final = await self._account_pool_settings()
        proxy_result: Final = await self._default_proxy(pool_settings)
        if isinstance(proxy_result, Failure):
            return proxy_result
        proxy_mode, proxy_profile_id, proxy_url = proxy_result.value
        now: Final = utc_now()
        record: Final = EnvironmentRecord(
            id=uuid4(),
            version=0,
            desired_state=EnvironmentStatus.PROVISIONING,
            operation_id=request.operation_id or str(uuid4()),
            name=request.name,
            provider=Provider.OPENAI,
            channel=request.channel,
            supplier=request.supplier,
            authorization_flow=supplier_definition.authorization_flow,
            status=EnvironmentStatus.PROVISIONING,
            enabled=True,
            manual_cooldown=False,
            concurrency_limit=pool_settings.default_concurrency_limit,
            proxy_mode=proxy_mode,
            proxy_profile_id=proxy_profile_id,
            desired_configuration=(
                EnvironmentConfiguration(
                    name=request.name,
                    concurrency_limit=pool_settings.default_concurrency_limit,
                    enabled=True,
                    manual_cooldown=False,
                    proxy_mode=proxy_mode,
                    proxy_profile_id=proxy_profile_id,
                    enabled_models=(),
                    proxy_url=proxy_url,
                    credential_enabled=True,
                )
                if proxy_mode is ProxyMode.PROFILE
                else None
            ),
            available_models=(),
            enabled_models=(),
            auth_file_name=None,
            auth_index=None,
            quota=QuotaSnapshot(),
            model_quotas=(),
            cooldown_until=None,
            oauth_state=None,
            oauth_expires_at=None,
            oauth_state_consumed_at=None,
            oauth_state_signature=None,
            oauth_provider_state=None,
            oauth_authorization_url=None,
            last_error=None,
            created_at=now,
            updated_at=now,
        )
        await self._repository.save(record)
        try:
            channel: Final = self._channel(record)
            await channel.provision(record)
            (
                provider_state,
                callback_state,
                callback_url,
                authorization_flow,
                authorization_user_code,
                expires_in_seconds,
            ) = await self._start_authorization(record)
            validated_authorization_url: Final = _HTTP_URL_ADAPTER.validate_python(callback_url)
        except Exception as error:
            failed: Final = record.model_copy(
                update={
                    "status": EnvironmentStatus.ERROR,
                    "desired_state": EnvironmentStatus.ERROR,
                    "last_error": _safe_error(error),
                    "updated_at": utc_now(),
                }
            )
            await self._repository.save(failed)
            await self._log_event(failed, "provisioning", error)
            return Failure(FailureCode.UPSTREAM, "environment provisioning failed")
        expires_at: Final = _authorization_expires_at(authorization_flow, expires_in_seconds)
        awaiting: Final = record.model_copy(
            update={
                "status": EnvironmentStatus.AWAITING_AUTHORIZATION,
                "desired_state": EnvironmentStatus.AWAITING_AUTHORIZATION,
                "oauth_state": callback_state,
                "oauth_expires_at": expires_at,
                "oauth_state_consumed_at": None,
                "oauth_state_signature": callback_state.rpartition(".")[2],
                "oauth_provider_state": provider_state,
                "oauth_authorization_url": callback_url,
                "authorization_flow": authorization_flow,
                "authorization_user_code": authorization_user_code,
                "updated_at": utc_now(),
            }
        )
        await self._repository.save(awaiting)
        command: Final = (
            (
                f"ssh -N -L {self._channels.channel(awaiting.channel).supplier(awaiting.supplier).callback_port}:127.0.0.1:"
                f"{self._settings.callback_port} {self._settings.ssh_user}@{self._settings.ssh_host}"
            )
            if awaiting.authorization_flow is AuthorizationFlow.BROWSER_OAUTH
            else None
        )
        return Success(
            AuthorizationView(
                environment=to_view(awaiting),
                flow=awaiting.authorization_flow,
                authorization_url=validated_authorization_url,
                ssh_command=command,
                user_code=awaiting.authorization_user_code,
                expires_at=expires_at,
            )
        )

    async def create_openai_compatible(self, request: CreateEnvironmentRequest) -> Result[EnvironmentView]:
        if request.provider_family != "openai_compatible" or request.openai_compatible is None:
            return Failure(FailureCode.INVALID, "openai_compatible configuration is required")
        configuration: Final = request.openai_compatible
        if request.operation_id is not None:
            existing: Final = await self._find_by_operation_id(request.operation_id)
            if existing is not None:
                return Success(to_view(existing))
        now: Final = utc_now()
        environment_id: Final = uuid4()
        cipher: Final = StateCipher(self._secrets)
        proxy_urls: Final = tuple(
            await asyncio.gather(
                *(
                    self._proxy_profiles.get_url(item.proxy_profile_id)
                    if item.proxy_profile_id is not None
                    else _constant_async(None)
                    for item in configuration.api_keys
                )
            )
        )
        if any(item.proxy_profile_id is not None and proxy_url is None for item, proxy_url in zip(configuration.api_keys, proxy_urls)):
            return Failure(FailureCode.INVALID, "proxy profile is unavailable")
        try:
            validated_proxy_urls: Final = tuple(
                validate_proxy_profile_url(proxy_url) if proxy_url is not None else None for proxy_url in proxy_urls
            )
        except ValueError:
            return Failure(FailureCode.INVALID, "proxy profile URL is invalid")
        encrypted_credentials: Final = tuple(
            {
                "api_key_ciphertext": cipher.seal(environment_id, item.api_key),
                "proxy_profile_id": item.proxy_profile_id,
                "proxy_url": proxy_url,
                "weight": item.weight,
            }
            for item, proxy_url in zip(configuration.api_keys, validated_proxy_urls)
        )
        encrypted_headers: Final = (
            cipher.seal(environment_id, json.dumps(configuration.headers, ensure_ascii=False))
            if configuration.headers
            else None
        )
        upstream_models: Final = tuple(dict.fromkeys(configuration.custom_models or (configuration.test_model,)))
        models: Final = tuple(
            f"{configuration.prefix}{model}" if configuration.prefix else model for model in upstream_models
        )
        pool_settings: Final = await self._account_pool_settings()
        proxy_result: Final = await self._default_proxy(pool_settings)
        if isinstance(proxy_result, Failure):
            return proxy_result
        proxy_mode, proxy_profile_id, proxy_url = proxy_result.value
        record: Final = EnvironmentRecord(
            id=environment_id,
            operation_id=request.operation_id or str(uuid4()),
            name=request.name,
            provider=Provider.OPENAI,
            channel=ChannelKind.OPENAI_COMPATIBLE,
            supplier=SupplierKind.OPENAI_COMPATIBLE,
            openai_compatible=OpenAICompatibleConfiguration(
                base_url=str(configuration.base_url),
                prefix=configuration.prefix,
                priority=configuration.priority,
                test_model=configuration.test_model,
                credentials=tuple(encrypted_credentials),
                headers_ciphertext=encrypted_headers,
                custom_models=upstream_models,
            ),
            desired_state=EnvironmentStatus.READY,
            status=EnvironmentStatus.VALIDATING,
            configuration_pending=True,
            desired_configuration_version=1,
            observed_configuration_version=0,
            enabled=True,
            manual_cooldown=False,
            concurrency_limit=pool_settings.default_concurrency_limit,
            proxy_mode=proxy_mode,
            proxy_profile_id=proxy_profile_id,
            desired_configuration=(
                EnvironmentConfiguration(
                    name=request.name,
                    concurrency_limit=pool_settings.default_concurrency_limit,
                    enabled=True,
                    manual_cooldown=False,
                    proxy_mode=proxy_mode,
                    proxy_profile_id=proxy_profile_id,
                    enabled_models=models,
                    proxy_url=proxy_url,
                    credential_enabled=True,
                )
                if proxy_mode is ProxyMode.PROFILE
                else None
            ),
            available_models=models,
            enabled_models=models,
            auth_file_name=None,
            auth_index=None,
            quota=QuotaSnapshot(),
            cooldown_until=None,
            automatic_cooldown=False,
            oauth_state=None,
            oauth_expires_at=None,
            oauth_state_consumed_at=None,
            oauth_state_signature=None,
            oauth_provider_state=None,
            oauth_authorization_url=None,
            authorization_flow=AuthorizationFlow.BROWSER_OAUTH,
            authorization_user_code=None,
            last_error=None,
            created_at=now,
            updated_at=now,
        )
        await self._repository.save(record)
        try:
            observed: Final = await self._channels.channel(ChannelKind.OPENAI_COMPATIBLE).read_account(record)
        except Exception as error:
            failed: Final = record.model_copy(
                update={
                    "status": EnvironmentStatus.ERROR,
                    "desired_state": EnvironmentStatus.ERROR,
                    "configuration_pending": False,
                    "last_error": _safe_error(error),
                    "updated_at": utc_now(),
                }
            )
            await self._repository.save(failed)
            return Failure(FailureCode.UPSTREAM, "OpenAI-compatible credential validation failed")
        ready: Final = observed.model_copy(
            update={
                "version": record.version + 1,
                "desired_state": EnvironmentStatus.READY,
                "status": EnvironmentStatus.READY,
                "configuration_pending": False,
                "observed_configuration_version": record.desired_configuration_version,
                "last_error": None,
                "updated_at": utc_now(),
            }
        )
        await self._repository.save(ready)
        return Success(to_view(ready))

    async def add_openai_compatible_credential(
        self,
        environment_id: UUID,
        request: OpenAICompatibleCredentialRequest,
    ) -> Result[EnvironmentView]:
        """把一个 API Key 加入指定卡片，并以卡片版本保证并发修改不会互相覆盖。"""
        lock: Final = await self._lock_for(environment_id)
        async with lock:
            record: Final = await self._repository.get(environment_id)
            if record is None:
                return Failure(FailureCode.NOT_FOUND, "environment not found")
            if record.channel is not ChannelKind.OPENAI_COMPATIBLE or record.openai_compatible is None:
                return Failure(FailureCode.INVALID, "credentials can only be added to OpenAI-compatible cards")
            if request.version != record.version:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            if request.proxy_profile_id is None:
                validated_proxy_url: Final = None
            else:
                profile_url: Final = await self._proxy_profiles.get_url(request.proxy_profile_id)
                if profile_url is None:
                    return Failure(FailureCode.INVALID, "proxy profile is unavailable")
                try:
                    validated_proxy_url: Final = validate_proxy_profile_url(profile_url)
                except ValueError:
                    return Failure(FailureCode.INVALID, "proxy profile URL is invalid")
            cipher: Final = StateCipher(self._secrets)
            credential: Final = OpenAICompatibleCredential(
                api_key_ciphertext=cipher.seal(environment_id, request.api_key),
                proxy_profile_id=request.proxy_profile_id,
                proxy_url=validated_proxy_url,
                weight=request.weight,
            )
            configuration: Final = record.openai_compatible.model_copy(
                update={"credentials": (*record.openai_compatible.credentials, credential)}
            )
            candidate: Final = record.model_copy(
                update={
                    "version": record.version + 1,
                    "openai_compatible": configuration,
                    "configuration_pending": True,
                    "desired_configuration_version": record.desired_configuration_version + 1,
                    "last_error": None,
                    "updated_at": utc_now(),
                }
            )
            saved: Final = await self._repository.save_if_version(candidate, record.version)
            if saved is None:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            try:
                observed: Final = await self._channels.channel(ChannelKind.OPENAI_COMPATIBLE).read_account(saved)
            except Exception as error:
                failed: Final = saved.model_copy(
                    update={"status": EnvironmentStatus.ERROR, "configuration_pending": False, "last_error": _safe_error(error), "updated_at": utc_now()}
                )
                await self._repository.save_if_version(failed, saved.version)
                return Failure(FailureCode.UPSTREAM, "OpenAI-compatible credential validation failed")
            ready: Final = observed.model_copy(
                update={
                    "version": saved.version + 1,
                    "status": EnvironmentStatus.READY if saved.enabled else EnvironmentStatus.DISABLED,
                    "configuration_pending": False,
                    "observed_configuration_version": saved.desired_configuration_version,
                    "last_error": None,
                    "updated_at": utc_now(),
                }
            )
            await self._repository.save_if_version(ready, saved.version)
            return Success(to_view(ready))

    async def delete_openai_compatible_credential(
        self,
        environment_id: UUID,
        request: OpenAICompatibleCredentialDeleteRequest,
    ) -> Result[EnvironmentView]:
        """删除单张 OpenAI 兼容凭据，至少保留一张凭据以避免卡片失去路由身份。"""
        lock: Final = await self._lock_for(environment_id)
        async with lock:
            record: Final = await self._repository.get(environment_id)
            if record is None:
                return Failure(FailureCode.NOT_FOUND, "environment not found")
            configuration: Final = record.openai_compatible
            if record.channel is not ChannelKind.OPENAI_COMPATIBLE or configuration is None:
                return Failure(FailureCode.INVALID, "credentials can only be removed from OpenAI-compatible cards")
            if request.version != record.version:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            if len(configuration.credentials) <= 1:
                return Failure(FailureCode.INVALID, "an OpenAI-compatible card must retain at least one credential")
            if request.credential_index >= len(configuration.credentials):
                return Failure(FailureCode.NOT_FOUND, "credential not found")
            credentials: Final = tuple(
                credential for index, credential in enumerate(configuration.credentials) if index != request.credential_index
            )
            candidate: Final = record.model_copy(
                update={
                    "version": record.version + 1,
                    "openai_compatible": configuration.model_copy(update={"credentials": credentials}),
                    "configuration_pending": True,
                    "desired_configuration_version": record.desired_configuration_version + 1,
                    "updated_at": utc_now(),
                }
            )
            saved: Final = await self._repository.save_if_version(candidate, record.version)
            if saved is None:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            try:
                observed: Final = await self._channels.channel(ChannelKind.OPENAI_COMPATIBLE).read_account(saved)
            except Exception as error:
                failed: Final = saved.model_copy(
                    update={"status": EnvironmentStatus.ERROR, "configuration_pending": False, "last_error": _safe_error(error), "updated_at": utc_now()}
                )
                await self._repository.save_if_version(failed, saved.version)
                return Failure(FailureCode.UPSTREAM, "OpenAI-compatible credential validation failed")
            ready: Final = observed.model_copy(
                update={
                    "version": saved.version + 1,
                    "status": EnvironmentStatus.READY if saved.enabled else EnvironmentStatus.DISABLED,
                    "configuration_pending": False,
                    "observed_configuration_version": saved.desired_configuration_version,
                    "last_error": None,
                    "updated_at": utc_now(),
                }
            )
            await self._repository.save_if_version(ready, saved.version)
            return Success(to_view(ready))

    async def authorize_environment(
        self,
        environment_id: UUID,
        operation_id: str | None = None,
    ) -> Result[AuthorizationView]:
        """为已有 Compose 环境创建新的、一次性的 OAuth state，不重建可复用资源。"""
        lock: Final = await self._lock_for(environment_id)
        async with lock:
            record: Final = await self._repository.get(environment_id)
            if record is None:
                return Failure(FailureCode.NOT_FOUND, "environment not found")
            if record.status is EnvironmentStatus.DELETING:
                return Failure(FailureCode.CONFLICT, "environment is being deleted")
            if (
                operation_id is not None
                and record.operation_id == operation_id
                and record.oauth_authorization_url is not None
                and record.oauth_expires_at is not None
                and record.oauth_state_consumed_at is None
                and record.oauth_expires_at > utc_now()
            ):
                return Success(self._authorization_view(record))
            try:
                channel: Final = self._channel(record)
                await channel.ensure_control_plane_connections(record.id)
                result: Final = await self._start_authorization(record)
                provider_state, callback_state, callback_url, flow, user_code, expires_in = result
                _HTTP_URL_ADAPTER.validate_python(callback_url)
            except Exception as error:
                await self._persist_authorization_failure(record, str(error))
                return Failure(FailureCode.UPSTREAM, "environment authorization failed")
            expires_at: Final = _authorization_expires_at(flow, expires_in)
            authorized: Final = record.model_copy(
                update={
                    "version": record.version + 1,
                    "status": EnvironmentStatus.AWAITING_AUTHORIZATION,
                    "desired_state": EnvironmentStatus.AWAITING_AUTHORIZATION,
                    "operation_id": operation_id or str(uuid4()),
                    "oauth_state": callback_state,
                    "oauth_expires_at": expires_at,
                    "oauth_state_consumed_at": None,
                    "oauth_state_signature": callback_state.rpartition(".")[2],
                    "oauth_provider_state": provider_state,
                    "oauth_authorization_url": callback_url,
                    "authorization_flow": flow,
                    "authorization_user_code": user_code,
                    "last_error": None,
                    "updated_at": utc_now(),
                }
            )
            saved: Final = await self._repository.save_if_version(authorized, record.version)
            if saved is None:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            return Success(self._authorization_view(saved))

    async def cancel_oauth_session(self, environment_id: UUID) -> Result[EnvironmentView]:
        record: Final = await self._repository.get(environment_id)
        if record is None:
            return Failure(FailureCode.NOT_FOUND, "environment not found")
        state: Final = record.oauth_provider_state or record.oauth_state
        if record.status is not EnvironmentStatus.AWAITING_AUTHORIZATION or state is None:
            return Failure(FailureCode.CONFLICT, "OAuth authorization is not pending")
        try:
            await self._channel(record).cancel_oauth_session(record, state)
        except Exception as error:
            await self._log_event(record, "authorization", error)
            return Failure(FailureCode.UPSTREAM, "OAuth session cancellation failed")
        restored_status: Final = (
            EnvironmentStatus.READY
            if record.auth_file_name is not None and record.enabled and not record.manual_cooldown
            else EnvironmentStatus.COOLING_DOWN
            if record.manual_cooldown
            else EnvironmentStatus.DISABLED
            if not record.enabled
            else EnvironmentStatus.AWAITING_AUTHORIZATION
        )
        cancelled: Final = record.model_copy(
            update={
                "version": record.version + 1,
                "status": restored_status,
                "desired_state": restored_status,
                "oauth_state": None,
                "oauth_expires_at": None,
                "oauth_state_signature": None,
                "oauth_provider_state": None,
                "oauth_authorization_url": None,
                "authorization_user_code": None,
                "last_error": None,
                "updated_at": utc_now(),
            }
        )
        saved: Final = await self._repository.save_if_version(cancelled, record.version)
        return Success(to_view(saved or cancelled))

    async def submit_oauth_callback(
        self,
        callback: OAuthCallback,
        environment_id: UUID | None = None,
    ) -> Result[EnvironmentView]:
        located: Final = await self._repository.find_by_oauth_state(callback.state)
        if located is None:
            return Failure(FailureCode.NOT_FOUND, "unknown or expired OAuth state")
        # 与重新授权共用环境锁，避免旧 callback 在新 state 写入后产生上游副作用。
        lock: Final = await self._lock_for(located.id)
        async with lock:
            current: Final = await self._repository.find_by_oauth_state(callback.state)
            if current is None:
                return Failure(FailureCode.NOT_FOUND, "unknown or expired OAuth state")
            return await self._submit_oauth_callback_locked(callback, environment_id, current)

    async def _submit_oauth_callback_locked(
        self,
        callback: OAuthCallback,
        environment_id: UUID | None,
        record: EnvironmentRecord,
    ) -> Result[EnvironmentView]:
        if environment_id is not None and record.id != environment_id:
            return Failure(FailureCode.CONFLICT, "OAuth state does not belong to this environment")
        if record.status is not EnvironmentStatus.AWAITING_AUTHORIZATION:
            return Failure(FailureCode.CONFLICT, "OAuth authorization is not pending")
        if record.oauth_state_consumed_at is not None:
            return Failure(FailureCode.CONFLICT, "OAuth callback has already been consumed")
        if record.oauth_state_signature is None or not self._valid_state_signature(record, callback.state):
            return Failure(FailureCode.CONFLICT, "invalid OAuth state")
        if record.oauth_state_consumed_at is None and (
            record.oauth_expires_at is None or record.oauth_expires_at <= utc_now()
        ):
            await self._persist_authorization_failure(record, "OAuth authorization expired")
            return Failure(FailureCode.CONFLICT, "OAuth authorization expired")
        consumed_at: Final = utc_now()
        consumed: Final = await self._consume_oauth_state(callback.state, consumed_at)
        if consumed is None:
            return Failure(FailureCode.CONFLICT, "OAuth callback has already been consumed")
        if environment_id is not None and consumed.id != environment_id:
            return Failure(FailureCode.CONFLICT, "OAuth state does not belong to this environment")
        if consumed.oauth_state_signature is None or not self._valid_state_signature(consumed, callback.state):
            return Failure(FailureCode.CONFLICT, "invalid OAuth state")
        if callback.code is None or not callback.code.strip():
            failure_reason: Final = (
                callback.error or callback.error_description or "OAuth authorization was not completed"
            )
            await self._persist_authorization_failure(consumed, failure_reason)
            return Failure(FailureCode.CONFLICT, "OAuth authorization was not completed")
        provider_state: Final = consumed.oauth_provider_state or callback.state
        provider_callback: Final = callback.model_copy(update={"state": provider_state})
        try:
            channel: Final = self._channel(consumed)
            await channel.submit_callback(consumed, provider_callback)
        except Exception as error:
            await self._persist_authorization_failure(consumed, str(error))
            return Failure(FailureCode.UPSTREAM, "OAuth callback failed")
        validating: Final = consumed.model_copy(
            update={
                "version": consumed.version + 1,
                "status": EnvironmentStatus.VALIDATING,
                "desired_state": EnvironmentStatus.VALIDATING,
                "oauth_expires_at": None,
                "oauth_provider_state": None,
                "oauth_authorization_url": None,
                "last_error": None,
                "updated_at": utc_now(),
            }
        )
        claimed: Final = await self._repository.save_if_version(validating, consumed.version)
        if claimed is None:
            return Failure(FailureCode.CONFLICT, "environment was changed by another request")
        try:
            validation: Final = await self._complete_authorization(claimed)
        except _AuthorizationConflict:
            return Failure(FailureCode.CONFLICT, "environment authorization is still being reconciled")
        if isinstance(validation, Failure):
            return validation
        validated: Final = validation.value
        if validated.status is EnvironmentStatus.READY and not self._gateway_environment(validated).routable:
            return Failure(FailureCode.CONFLICT, "environment authorization is still being reconciled")
        return Success(to_view(validated))

    async def _find_by_operation_id(self, operation_id: str) -> EnvironmentRecord | None:
        return await self._repository.find_by_operation_id(operation_id)

    async def _consume_oauth_state(self, state: str, consumed_at: datetime) -> EnvironmentRecord | None:
        return await self._repository.consume_oauth_state(state, consumed_at)

    async def _persist_authorization_failure(
        self,
        record: EnvironmentRecord,
        message: str,
    ) -> EnvironmentRecord:
        failed: Final = record.model_copy(
            update={
                "version": record.version + 1,
                "status": EnvironmentStatus.ERROR,
                "desired_state": EnvironmentStatus.ERROR,
                "last_error": _safe_error(RuntimeError(message)),
                "oauth_state": None,
                "oauth_expires_at": None,
                "oauth_state_signature": None,
                "oauth_provider_state": None,
                "oauth_authorization_url": None,
                "updated_at": utc_now(),
            }
        )
        saved: Final = await self._repository.save_if_version(failed, record.version)
        if saved is not None:
            await self._log_event(saved, "authorization", RuntimeError(message))
        return saved or await self._repository.get(record.id) or record

    def _state_signature(self, environment_id: UUID, state: str) -> str:
        key: Final = self._secrets.derive(environment_id, SecretPurpose.OAUTH_STATE).encode("ascii")
        message: Final = f"{environment_id.hex}:{state}".encode()
        return hmac.new(key, message, hashlib.sha256).hexdigest()

    def _callback_state(self, record: EnvironmentRecord) -> str:
        nonce: Final = token_secrets.token_urlsafe(32)
        signature: Final = self._state_signature(record.id, nonce)
        # state 本身不携带凭据，只使用随机值和环境绑定签名，防止跨环境转发与重放。
        return f"{nonce}.{signature}"

    def _valid_state_signature(self, record: EnvironmentRecord, state: str) -> bool:
        nonce, separator, signature = state.rpartition(".")
        if not separator or not nonce or not signature:
            return False
        expected: Final = self._state_signature(record.id, nonce)
        return hmac.compare_digest(signature, expected) and (
            record.oauth_state_signature is None or hmac.compare_digest(record.oauth_state_signature, signature)
        )

    def _authorization_view(self, record: EnvironmentRecord) -> AuthorizationView:
        if record.oauth_authorization_url is None or record.oauth_expires_at is None:
            raise RuntimeError("authorization operation has no active credentials")
        supplier: Final = self._channels.get(record.channel).supplier(record.supplier)
        return AuthorizationView(
            environment=to_view(record),
            flow=record.authorization_flow,
            authorization_url=_HTTP_URL_ADAPTER.validate_python(record.oauth_authorization_url),
            ssh_command=(
                (
                    f"ssh -N -L {supplier.callback_port}:127.0.0.1:{self._settings.callback_port} "
                    f"{self._settings.ssh_user}@{self._settings.ssh_host}"
                )
                if record.authorization_flow is AuthorizationFlow.BROWSER_OAUTH and supplier.callback_port is not None
                else None
            ),
            user_code=record.authorization_user_code,
            expires_at=record.oauth_expires_at,
        )

    async def _validate_authorized(self, record: EnvironmentRecord) -> EnvironmentRecord:
        started_at: Final = record.oauth_state_consumed_at or record.created_at
        remaining: Final = (started_at + _AUTHORIZATION_VALIDATION_TIMEOUT - utc_now()).total_seconds()
        if remaining <= 0:
            return await self._persist_validation_retry(record, "Account channel did not become ready")
        try:
            # 限制包含底层连接退避在内的总耗时，避免 SDK 重试长期占住环境锁。
            async with asyncio.timeout(min(15.0, remaining)):
                channel: Final = self._channel(record)
                observed: Final = await channel.read_account(record)
                healthy: Final = await channel.data_plane_health_check(observed)
        except Exception as error:
            return await self._persist_validation_retry(record, _safe_error(error))
        if not healthy:
            return await self._persist_validation_retry(record, "Account channel data plane validation failed")
        return observed

    async def _persist_validation_retry(self, record: EnvironmentRecord, message: str) -> EnvironmentRecord:
        now: Final = utc_now()
        # 授权消费时间已持久化，重复检查和 Manager 重启都不能延长启动等待期限。
        started_at: Final = record.oauth_state_consumed_at or record.created_at
        expired: Final = now - started_at >= _AUTHORIZATION_VALIDATION_TIMEOUT
        status: Final = EnvironmentStatus.ERROR if expired else EnvironmentStatus.VALIDATING
        updated: Final = record.model_copy(
            update={
                "version": record.version + 1,
                "status": status,
                "desired_state": status,
                "last_error": (
                    f"Account channel startup validation timed out: {message}"
                    if expired else f"Waiting for account channel startup; retrying: {message}"
                ),
                "updated_at": now,
            }
        )
        saved: Final = await self._repository.save_if_version(updated, record.version)
        if saved is None:
            raise _AuthorizationConflict
        await self._log_event(saved, "validation", RuntimeError(message), retryable=not expired)
        return saved

    async def _complete_authorization(self, record: EnvironmentRecord) -> Result[EnvironmentRecord]:
        validated: Final = await self._validate_authorized(record)
        if validated.status is EnvironmentStatus.VALIDATING:
            return Failure(FailureCode.CONFLICT, "Account authorization received; waiting for channel startup")
        if validated.status not in _AUTHORIZATION_COMPLETE_STATUSES:
            return Failure(FailureCode.UPSTREAM, "environment authorization validation failed")
        completed: Final = validated.model_copy(
            update={
                "version": record.version + 1,
                "status": validated.status,
                "desired_state": validated.status,
                "oauth_expires_at": None,
                "oauth_provider_state": None,
                "oauth_authorization_url": None,
                "last_error": None,
                "updated_at": utc_now(),
            }
        )
        if completed.status is not EnvironmentStatus.READY:
            normalized: Final = completed.model_copy(
                update={
                    "configuration_pending": False,
                    "observed_configuration_version": completed.desired_configuration_version,
                    "configuration_last_error": None,
                }
            )
            saved_completed: Final = await self._repository.save_if_version(normalized, record.version)
            if saved_completed is None:
                raise _AuthorizationConflict
            return Success(saved_completed)
        initial_desired: Final = completed.desired_configuration or configuration_from_record(completed)
        desired: Final = (
            initial_desired.model_copy(update={"enabled_models": completed.enabled_models})
            if not initial_desired.enabled_models and completed.enabled_models
            else initial_desired
        )
        pending: Final = completed.model_copy(
            update={
                "configuration_pending": True,
                "desired_configuration_version": completed.desired_configuration_version + 1,
                "desired_configuration": desired,
                "configuration_last_error": None,
            }
        )
        claimed: Final = await self._repository.save_if_version(pending, record.version)
        if claimed is None:
            raise _AuthorizationConflict
        reconciled: Final = await self._apply_and_persist_configuration(claimed, desired)
        if isinstance(reconciled, Failure):
            return reconciled
        persisted: Final = await self._repository.get(claimed.id)
        if persisted is None or persisted.status not in _AUTHORIZATION_COMPLETE_STATUSES:
            raise _AuthorizationConflict
        if persisted.status is EnvironmentStatus.READY and not self._gateway_environment(persisted).routable:
            raise _AuthorizationConflict
        return Success(persisted)

    async def _persist_cleanup_progress(
        self,
        record: EnvironmentRecord,
        progress: CleanupProgress,
    ) -> EnvironmentRecord | None:
        updated: Final = record.model_copy(update={"cleanup_progress": progress, "updated_at": utc_now()})
        return await self._repository.save_if_version(updated, record.version)

    async def update_environment(
        self,
        environment_id: UUID,
        request: UpdateEnvironmentRequest,
    ) -> Result[EnvironmentView]:
        lock: Final = await self._lock_for(environment_id)
        async with lock:
            record: Final = await self._repository.get(environment_id)
            if record is None:
                return Failure(FailureCode.NOT_FOUND, "environment not found")
            if request.operation_id is not None and record.operation_id == request.operation_id:
                if (
                    record.configuration_pending
                    or record.desired_configuration_version > record.observed_configuration_version
                ):
                    desired: Final = record.desired_configuration or configuration_from_record(record)
                    return await self._apply_and_persist_configuration(record, desired)
                return Success(to_view(record))
            if record.auth_file_name is None and record.status not in (
                EnvironmentStatus.AWAITING_AUTHORIZATION, EnvironmentStatus.ERROR,
            ):
                if record.channel is not ChannelKind.OPENAI_COMPATIBLE:
                    return Failure(FailureCode.CONFLICT, "environment authorization is not complete")
            if request.version != record.version:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            if (
                record.configuration_pending
                or record.desired_configuration_version > record.observed_configuration_version
            ):
                return Failure(FailureCode.CONFLICT, "environment configuration is still being applied")
            unknown_models: Final = frozenset(request.enabled_models).difference(record.available_models)
            if unknown_models:
                return Failure(FailureCode.INVALID, "enabled_models contains unsupported models")
            profile_result: Final = await self._resolve_proxy(request)
            if isinstance(profile_result, Failure):
                return profile_result
            automatic_cooldown: Final = await self._automatic_cooldown_before_update(record, request.manual_cooldown)
            credential_enabled: Final = (
                request.enabled
                and not request.manual_cooldown
                and automatic_cooldown
                in (
                    _AutomaticCooldownState.NONE,
                    _AutomaticCooldownState.RECOVERED,
                )
            )
            status: Final = _status_after_update(record, request, automatic_cooldown)
            cooldown_until: Final = _cooldown_until_after_update(record, request.manual_cooldown, automatic_cooldown)
            desired_configuration: Final = EnvironmentConfiguration(
                name=request.name,
                concurrency_limit=request.concurrency_limit,
                enabled=request.enabled,
                manual_cooldown=request.manual_cooldown,
                proxy_mode=request.proxy_mode,
                proxy_profile_id=request.proxy_profile_id,
                enabled_models=request.enabled_models,
                proxy_url=profile_result.value,
                credential_enabled=credential_enabled,
            )
            updated: Final = record.model_copy(
                update={
                    "name": request.name,
                    "version": record.version + 1,
                    "configuration_pending": True,
                    "desired_state": status,
                    "operation_id": request.operation_id or str(uuid4()),
                    "desired_configuration_version": record.desired_configuration_version + 1,
                    "desired_configuration": desired_configuration,
                    "configuration_last_error": None,
                    "concurrency_limit": request.concurrency_limit,
                    "enabled": request.enabled,
                    "manual_cooldown": request.manual_cooldown,
                    "proxy_mode": request.proxy_mode,
                    "proxy_profile_id": request.proxy_profile_id,
                    "enabled_models": request.enabled_models,
                    "status": status,
                    "cooldown_until": cooldown_until,
                    "automatic_cooldown": automatic_cooldown
                    in (
                        _AutomaticCooldownState.ACTIVE,
                        _AutomaticCooldownState.BLOCKED,
                    ),
                    "last_error": None,
                    "updated_at": utc_now(),
                }
            )
            claimed: Final = await self._repository.save_if_version(updated, record.version)
            if claimed is None:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            return await self._apply_and_persist_configuration(claimed, desired_configuration)

    async def reconcile_pending_configurations(self) -> tuple[EnvironmentView, ...]:
        """Manager 启动或后台循环时重复收敛所有未完成配置操作。"""
        records: Final = await self._repository.list()
        results: Final = await asyncio.gather(
            *(
                self._reconcile_configuration(record)
                for record in records
                if _configuration_requires_reconciliation(record)
            )
        )
        return tuple(result.value for result in results if isinstance(result, Success))

    async def reconcile_pending_authorizations(self) -> None:
        """关闭页面后仍由后台继续验证已接收的授权，不重复领取或写入凭据。"""
        records: Final = await self._repository.list()
        await asyncio.gather(*(
            self._refresh_if_needed(record)
            for record in records
            if record.status is EnvironmentStatus.VALIDATING
        ))

    async def _reconcile_configuration(self, record: EnvironmentRecord) -> Result[EnvironmentView]:
        lock: Final = await self._lock_for(record.id)
        async with lock:
            # 锁等待期间记录可能已删除、完成或进入删除态，重新读取后禁止执行陈旧副作用。
            current: Final = await self._repository.get(record.id)
            if current is None:
                return Failure(FailureCode.NOT_FOUND, "environment not found")
            if current.status is EnvironmentStatus.DELETING or not _configuration_requires_reconciliation(current):
                return Success(to_view(current))
            desired: Final = current.desired_configuration or configuration_from_record(current)
            return await self._apply_and_persist_configuration(current, desired)

    async def _apply_and_persist_configuration(
        self,
        record: EnvironmentRecord,
        desired: EnvironmentConfiguration,
    ) -> Result[EnvironmentView]:
        try:
            channel: Final = self._channel(record)
            await channel.apply_configuration(record, desired)
        except Exception as error:
            # 失败持久化后的版本已变化，必须以该版本完成 ERROR + pending 检查点写入。
            failed: Final = record.model_copy(
                update={
                    "version": record.version + 1,
                    "configuration_pending": True,
                    "status": EnvironmentStatus.ERROR,
                    "desired_state": record.desired_state or record.status,
                    "configuration_last_error": _safe_error(error),
                    "last_error": _safe_error(error),
                    "updated_at": utc_now(),
                }
            )
            saved_failed: Final = await self._repository.save_if_version(failed, record.version)
            if saved_failed is None:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            await self._log_event(saved_failed, "configuration", error, retryable=True)
            return Failure(FailureCode.UPSTREAM, "environment configuration failed")
        completed: Final = record.model_copy(
            update={
                "configuration_pending": False,
                "observed_configuration_version": record.desired_configuration_version,
                "configuration_last_error": None,
                "last_error": None,
                "status": record.desired_state or record.status,
                "updated_at": utc_now(),
            }
        )
        saved: Final = await self._repository.save_if_version(completed, record.version)
        if saved is None:
            return Failure(FailureCode.CONFLICT, "environment was changed by another request")
        await self._log_event(saved, "configuration", None)
        return Success(to_view(saved))

    async def delete_environment(self, environment_id: UUID, operation_id: str | None = None) -> Result[None]:
        lock: Final = await self._lock_for(environment_id)
        async with lock:
            record: Final = await self._repository.get(environment_id)
            if record is None:
                return Success(None)
            requested_operation: Final = operation_id or record.operation_id or str(uuid4())
            if record.status is EnvironmentStatus.DELETING and operation_id is not None:
                if record.operation_id not in (None, operation_id):
                    return Failure(FailureCode.CONFLICT, "environment deletion is owned by another operation")
            deleting: EnvironmentRecord | None = (
                record
                if record.status is EnvironmentStatus.DELETING
                else await self._repository.save_if_version(
                    record.model_copy(
                        update={
                            "version": record.version + 1,
                            "status": EnvironmentStatus.DELETING,
                            "desired_state": EnvironmentStatus.DELETING,
                            "enabled": False,
                            "operation_id": requested_operation,
                            "cleanup_progress": CleanupProgress(),
                            "updated_at": utc_now(),
                        }
                    ),
                    record.version,
                )
            )
            if deleting is None:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")

            progress: CleanupProgress = deleting.cleanup_progress
            deleting_with_routes: Final = (
                deleting
                if progress.routes_removed
                else await self._persist_cleanup_progress(
                    deleting,
                    routes_removed(progress),
                )
            )
            if deleting_with_routes is None:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            try:
                deleting_with_compose: Final = await self._remove_compose_step(deleting_with_routes)
            except Exception as error:
                failed_compose: Final = deleting_with_routes.model_copy(
                    update={
                        "last_error": _safe_error(error),
                        "configuration_last_error": _safe_error(error),
                        "updated_at": utc_now(),
                    }
                )
                await self._repository.save_if_version(failed_compose, deleting_with_routes.version)
                await self._log_event(failed_compose, "cleanup", error, retryable=True)
                return Failure(FailureCode.UPSTREAM, "environment cleanup failed")
            if deleting_with_compose is None:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            try:
                deleting_with_directory: Final = await self._remove_directory_step(deleting_with_compose)
            except Exception as error:
                failed_directory: Final = deleting_with_compose.model_copy(
                    update={
                        "last_error": _safe_error(error),
                        "configuration_last_error": _safe_error(error),
                        "updated_at": utc_now(),
                    }
                )
                await self._repository.save_if_version(failed_directory, deleting_with_compose.version)
                await self._log_event(failed_directory, "cleanup", error, retryable=True)
                return Failure(FailureCode.UPSTREAM, "environment cleanup failed")
            if deleting_with_directory is None:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            try:
                await self._repository.delete(environment_id)
            except Exception as error:
                failed_delete: Final = deleting_with_directory.model_copy(
                    update={"last_error": _safe_error(error), "updated_at": utc_now()}
                )
                await self._repository.save_if_version(failed_delete, deleting_with_directory.version)
                await self._log_event(failed_delete, "cleanup", error, retryable=True)
                return Failure(FailureCode.UPSTREAM, "environment metadata cleanup failed")
            await self._log_event(deleting_with_directory, "cleanup", None)
            return Success(None)

    async def _remove_compose_step(self, record: EnvironmentRecord) -> EnvironmentRecord | None:
        if record.cleanup_progress.compose_removed:
            return record
        if record.channel is ChannelKind.OPENAI_COMPATIBLE:
            return await self._persist_cleanup_progress(record, compose_removed(record.cleanup_progress))
        channel: Final = self._channel(record)
        await channel.remove_compose(record)
        return await self._persist_cleanup_progress(record, compose_removed(record.cleanup_progress))

    async def _remove_directory_step(self, record: EnvironmentRecord) -> EnvironmentRecord | None:
        if record.cleanup_progress.directory_removed:
            return record
        if record.channel is ChannelKind.OPENAI_COMPATIBLE:
            return await self._persist_cleanup_progress(record, directory_removed())
        channel: Final = self._channel(record)
        await channel.remove_directory(record.id)
        return await self._persist_cleanup_progress(record, directory_removed())

    async def _automatic_cooldown_before_update(
        self,
        record: EnvironmentRecord,
        manual_cooldown: bool,
    ) -> _AutomaticCooldownState:
        # 自动冷却必须先通过真实数据面探活，配置保存不能成为绕过额度保护的入口。
        if manual_cooldown:
            return _AutomaticCooldownState.ACTIVE if record.cooldown_until is not None else _AutomaticCooldownState.NONE
        if record.status is EnvironmentStatus.AWAITING_AUTHORIZATION:
            # 授权完成前数据面必然不健康，探活只会误报 BLOCKED，保持等待授权状态即可。
            return _AutomaticCooldownState.NONE
        if record.cooldown_until is not None:
            if record.cooldown_until > utc_now():
                return _AutomaticCooldownState.ACTIVE
            return (
                _AutomaticCooldownState.RECOVERED
                if await self._data_plane_health_check(record)
                else _AutomaticCooldownState.BLOCKED
            )
        if record.manual_cooldown:
            return (
                _AutomaticCooldownState.RECOVERED
                if await self._data_plane_health_check(record)
                else _AutomaticCooldownState.BLOCKED
            )
        return (
            _AutomaticCooldownState.ACTIVE
            if record.status == EnvironmentStatus.COOLING_DOWN
            else _AutomaticCooldownState.NONE
        )

    async def _data_plane_health_check(self, record: EnvironmentRecord) -> bool:
        channel: Final = self._channel(record)
        return await channel.data_plane_health_check(record)

    async def _refresh_if_needed(self, record: EnvironmentRecord) -> EnvironmentRecord:
        if record.status not in (
            EnvironmentStatus.AWAITING_AUTHORIZATION,
            EnvironmentStatus.VALIDATING,
            EnvironmentStatus.READY,
            EnvironmentStatus.COOLING_DOWN,
            EnvironmentStatus.DISABLED,
        ):
            return record
        lock: Final = await self._lock_for(record.id)
        if lock.locked():
            return record
        async with lock:
            current: Final = await self._repository.get(record.id) or record
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
            if current.automatic_cooldown and not await channel.data_plane_health_check(current):
                return current
            if _cooldown_active(current):
                return current
            if _cooldown_elapsed(current) and not await channel.data_plane_health_check(current):
                return current
            try:
                observed: Final = await channel.read_account(current)
            except Exception as error:
                await self._log_event(current, "quota", error)
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
            return await self._repository.save(refreshed)

    async def _reloaded_consumed_state(
        self,
        record: EnvironmentRecord,
        state: str,
    ) -> EnvironmentRecord | None:
        durable: Final = await self._repository.get(record.id) or record
        return (
            durable
            if durable.status is EnvironmentStatus.AWAITING_AUTHORIZATION
            and durable.oauth_state == state
            and durable.oauth_state_consumed_at is not None
            else None
        )

    async def _update_authorization_error(self, record: EnvironmentRecord, message: str | None) -> EnvironmentRecord:
        if record.last_error == message:
            return record
        updated: Final = record.model_copy(
            update={"version": record.version + 1, "last_error": message, "updated_at": utc_now()}
        )
        saved: Final = await self._repository.save_if_version(updated, record.version)
        return saved or await self._repository.get(record.id) or record

    async def _refresh_authorization(self, record: EnvironmentRecord) -> EnvironmentRecord:
        if record.oauth_state is None or record.oauth_expires_at is None:
            return record
        if record.oauth_expires_at <= utc_now():
            return await self._persist_authorization_failure(record, "OAuth authorization expired")
        if record.oauth_state_signature is None or not self._valid_state_signature(record, record.oauth_state):
            return await self._persist_authorization_failure(record, "invalid OAuth state")
        try:
            channel: Final = self._channel(record)
            status: Final = await channel.authorization_status(record, record.oauth_provider_state or record.oauth_state)
        except Exception as error:
            # 展示脱敏后的失败原因并保留授权状态，短暂断网或写入失败后仍可重试。
            return await self._update_authorization_error(record, _safe_error(error))
        if status == "wait":
            return await self._update_authorization_error(record, None)
        if status.startswith("error:"):
            return await self._persist_authorization_failure(record, status.removeprefix("error:"))
        if status != "ok":
            return record
        consumed_at: Final = utc_now()
        consumed_result: Final = await self._consume_oauth_state(record.oauth_state, consumed_at)
        consumed: Final = (
            consumed_result
            if consumed_result is not None
            else await self._reloaded_consumed_state(record, record.oauth_state)
        )
        if consumed is None:
            return await self._repository.get(record.id) or record
        if consumed.oauth_state_signature is None or not self._valid_state_signature(consumed, record.oauth_state):
            return await self._persist_authorization_failure(consumed, "invalid OAuth state")
        validating: Final = consumed.model_copy(
            update={
                "version": consumed.version + 1,
                "status": EnvironmentStatus.VALIDATING,
                "desired_state": EnvironmentStatus.VALIDATING,
                "oauth_expires_at": None,
                "oauth_authorization_url": None,
                "oauth_provider_state": None,
                "last_error": None,
                "updated_at": utc_now(),
            }
        )
        claimed: Final = await self._repository.save_if_version(validating, consumed.version)
        if claimed is None:
            return await self._repository.get(record.id) or record
        try:
            completion: Final = await self._complete_authorization(claimed)
        except _AuthorizationConflict:
            return await self._repository.get(record.id) or record
        if isinstance(completion, Failure):
            return await self._repository.get(claimed.id) or record
        return completion.value

    async def _resolve_proxy(self, request: UpdateEnvironmentRequest) -> Result[str]:
        if request.proxy_mode == ProxyMode.DEFAULT_GATEWAY:
            return Success("")
        if request.proxy_profile_id is None:
            return Failure(FailureCode.INVALID, "proxy profile is required")
        profile_url: Final = await self._proxy_profiles.get_url(request.proxy_profile_id)
        if profile_url is None:
            return Failure(FailureCode.INVALID, "proxy profile is unavailable")
        try:
            validated_url: Final = validate_proxy_profile_url(profile_url)
        except ValueError:
            return Failure(FailureCode.INVALID, "proxy profile URL is invalid")
        return Success(validated_url)

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


def _authorization_expires_at(flow: AuthorizationFlow, expires_in_seconds: int | None) -> datetime:
    duration: Final = expires_in_seconds if flow is AuthorizationFlow.DEVICE_CODE and expires_in_seconds is not None else 300
    return utc_now() + timedelta(seconds=min(max(duration, 1), 3600))


def _configuration_requires_reconciliation(record: EnvironmentRecord) -> bool:
    # ERROR 只代表最近一次尝试失败，期望版本未观测时仍须继续补偿。
    return record.configuration_pending or record.desired_configuration_version > record.observed_configuration_version


def _safe_error(error: Exception) -> str:
    return safe_error(error)


def _replace_state(authorization_url: str, state: str) -> str:
    """只替换 OAuth URL 的 state 参数，保留上游其余参数并避免把 state 拼进日志。"""
    try:
        parsed: Final = urlsplit(authorization_url)
        query: Final = tuple(
            (key, state if key == "state" else value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        )
        final_query: Final = query if any(key == "state" for key, _ in query) else (*query, ("state", state))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(final_query), parsed.fragment))
    except ValueError:
        return authorization_url


def _cooldown_elapsed(record: EnvironmentRecord) -> bool:
    return (
        record.enabled
        and not record.manual_cooldown
        and record.cooldown_until is not None
        and record.cooldown_until <= utc_now()
    )


def _cooldown_active(record: EnvironmentRecord) -> bool:
    return record.cooldown_until is not None and record.cooldown_until > utc_now()


def _status_after_update(
    record: EnvironmentRecord,
    request: UpdateEnvironmentRequest,
    automatic_cooldown: _AutomaticCooldownState,
) -> EnvironmentStatus:
    # 授权失败时允许修正代理，但保存配置不能把未授权账号变成可用账号。
    if record.auth_file_name is None:
        return record.status
    if not request.enabled:
        return EnvironmentStatus.DISABLED
    if request.manual_cooldown:
        return EnvironmentStatus.COOLING_DOWN
    if automatic_cooldown in (_AutomaticCooldownState.ACTIVE, _AutomaticCooldownState.BLOCKED):
        return EnvironmentStatus.COOLING_DOWN
    if record.status in (EnvironmentStatus.AWAITING_AUTHORIZATION, EnvironmentStatus.VALIDATING):
        return record.status
    return EnvironmentStatus.READY


def _cooldown_until_after_update(
    record: EnvironmentRecord,
    manual_cooldown: bool,
    automatic_cooldown: _AutomaticCooldownState,
) -> datetime | None:
    if manual_cooldown:
        return record.cooldown_until
    return None if automatic_cooldown is _AutomaticCooldownState.RECOVERED else record.cooldown_until
