"""创建供应商环境并验证初始凭据，保留幂等和失败清理顺序。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final
from uuid import UUID, uuid4

from account_pool.application.authorization import (
    _authorization_expires_at,
    _replace_state,
)
from account_pool.application.environments.contracts import HTTP_URL_ADAPTER as _HTTP_URL_ADAPTER
from account_pool.application.environments.contracts import (
    AccountPoolSettingsOperation,
    ApplyAndPersistConfigurationOperation,
    AuthorizationViewOperation,
    AuthorizeEnvironmentOperation,
    CallbackStateOperation,
    ChannelOperation,
    DefaultProxyOperation,
    FindByOperationIdOperation,
    LockForOperation,
    LogEventOperation,
    UploadAuthFileOperation,
)
from account_pool.application.environments.contracts import constant_async as _constant_async
from account_pool.channels.registry import ChannelRegistry, UnsupportedChannelError
from account_pool.config import Settings, validate_proxy_profile_url
from account_pool.credential_ownership import CredentialConflict, CredentialOwnership, credential_identity
from account_pool.domain import (
    AuthorizationFlow,
    AuthorizationInstructionFlow,
    AuthorizationView,
    ChannelKind,
    CreateDirectCredentialEnvironmentRequest,
    CreateEnvironmentRequest,
    CreateVertexEnvironmentRequest,
    EnvironmentConfiguration,
    EnvironmentRecord,
    EnvironmentStatus,
    EnvironmentView,
    OpenAICompatibleConfiguration,
    OpenAICompatibleCredentialDeleteRequest,
    OpenAICompatibleCredentialRequest,
    Provider,
    ProxyMode,
    QuotaSnapshot,
    SupplierKind,
    configuration_from_record,
    to_view,
    utc_now,
)
from account_pool.ports import (
    CLIProxyClient,
    EnvironmentChannel,
    EnvironmentRepository,
    ProxyProfileRepository,
)
from account_pool.shared.error_safety import safe_error as _safe_error
from account_pool.shared.result import Failure, FailureCode, Result, Success
from account_pool.shared.secrets import EnvironmentSecretDeriver, StateCipher


@dataclass(frozen=True, slots=True)
class EnvironmentProvisioning:
    _account_pool_settings: AccountPoolSettingsOperation
    _apply_and_persist_configuration: ApplyAndPersistConfigurationOperation
    _authorization_view: AuthorizationViewOperation
    _callback_state: CallbackStateOperation
    _channel: ChannelOperation
    _channels: ChannelRegistry
    _cli_proxy: CLIProxyClient
    _default_proxy: DefaultProxyOperation
    _direct_credential_validation_interval_seconds: float
    _direct_credential_validation_timeout_seconds: float
    _find_by_operation_id: FindByOperationIdOperation
    _lock_for: LockForOperation
    _log_event: LogEventOperation
    _ownership: CredentialOwnership
    _proxy_profiles: ProxyProfileRepository
    _repository: EnvironmentRepository
    _secrets: EnvironmentSecretDeriver
    _settings: Settings
    authorize_environment: AuthorizeEnvironmentOperation
    upload_auth_file: UploadAuthFileOperation

    async def start_authorization(
        self,
        record: EnvironmentRecord,
    ) -> tuple[str, str, str, AuthorizationInstructionFlow, str | None, int | None]:
        channel: Final = self._channel(record)
        result: Final = await channel.start_authorization(record)
        supplier: Final = channel.supplier(record.supplier)
        if supplier.authorization_flow is AuthorizationFlow.DIRECT_CREDENTIAL:
            raise RuntimeError("direct credential suppliers do not use authorization instructions")
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

    async def create_environment(
        self, request: CreateEnvironmentRequest, *, environment_id: UUID | None = None
    ) -> Result[AuthorizationView]:
        try:
            channel_definition: Final = self._channels.get(request.channel)
            supplier_definition: Final = channel_definition.supplier(request.supplier)
        except (KeyError, UnsupportedChannelError) as error:
            return Failure(FailureCode.INVALID, str(error))
        if supplier_definition.authorization_flow is AuthorizationFlow.DIRECT_CREDENTIAL:
            return Failure(FailureCode.INVALID, "direct credential suppliers require the credential creation endpoint")
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
            id=environment_id or uuid4(),
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
            ) = await self.start_authorization(record)
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

    async def create_direct_credential_environment(
        self, request: CreateDirectCredentialEnvironmentRequest
    ) -> Result[EnvironmentView]:
        return await self.provision_direct_credential_environment(
            name=request.name,
            supplier=request.supplier,
            operation_id=request.operation_id,
            credential_content=json.dumps({"api_key": request.credential.api_key}).encode(),
            write_credential=lambda channel, record, proxy_url: channel.write_direct_api_key(
                record, request.credential, proxy_url
            ),
        )

    async def create_auth_file_environment(
        self, request: CreateEnvironmentRequest, environment_id: UUID, filename: str, content: bytes
    ) -> Result[EnvironmentView]:
        existing: Final = await self._repository.get(environment_id)
        if existing is not None:
            if existing.status is EnvironmentStatus.DELETING or not existing.enabled or existing.manual_cooldown:
                return Failure(FailureCode.CONFLICT, "卡片已停用、冷却或删除，请先检查卡片状态")
            if existing.status is EnvironmentStatus.READY and not existing.configuration_pending:
                return Success(to_view(existing))
            try:
                await self._channel(existing).provision(existing)
            except Exception:
                return Failure(FailureCode.UPSTREAM, "卡片运行环境启动失败，可重试原任务")
            return await self.upload_auth_file(environment_id, filename, content, "application/json", replace=True)
        lock: Final = await self._lock_for(environment_id)
        async with lock, self._ownership.operation(environment_id):
            return await self.provision_direct_credential_environment(
                name=request.name,
                supplier=request.supplier,
                operation_id=request.operation_id,
                credential_content=content,
                write_credential=lambda channel, record, _: self._cli_proxy.upload_auth_file(
                    record, filename, content, "application/json"
                ),
                auth_file=True,
                environment_id=environment_id,
            )

    async def pending_authorization(self, environment_id: UUID) -> Result[AuthorizationView]:
        record: Final = await self._repository.get(environment_id)
        if record is None or record.oauth_authorization_url is None or record.oauth_expires_at is None:
            return Failure(FailureCode.NOT_FOUND, "暂无待完成的授权")
        if record.oauth_expires_at <= utc_now() or record.oauth_state_consumed_at is not None:
            return Failure(FailureCode.CONFLICT, "授权已过期或完成，请刷新任务")
        return Success(self._authorization_view(record))

    async def resume_onboarding_oauth(self, environment_id: UUID) -> Result[AuthorizationView]:
        record: Final = await self._repository.get(environment_id)
        if (
            record is None
            or record.status is EnvironmentStatus.DELETING
            or not record.enabled
            or record.manual_cooldown
        ):
            return Failure(FailureCode.CONFLICT, "卡片不可恢复，请先检查状态")
        try:
            await self._channel(record).provision(record)
        except Exception:
            return Failure(FailureCode.UPSTREAM, "卡片运行环境恢复失败")
        return await self.authorize_environment(environment_id)

    async def create_vertex_environment(
        self,
        request: CreateVertexEnvironmentRequest,
        filename: str,
        content: bytes,
    ) -> Result[EnvironmentView]:
        return await self.provision_direct_credential_environment(
            name=request.name,
            supplier=SupplierKind.VERTEX,
            operation_id=request.operation_id,
            credential_content=content,
            write_credential=lambda channel, record, _: channel.import_vertex_credential(
                record, filename, content, request.location
            ),
        )

    async def provision_direct_credential_environment(
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
        try:
            supplier_definition: Final = self._channels.get(ChannelKind.CLIPROXYAPI).supplier(supplier)
        except (KeyError, UnsupportedChannelError) as error:
            return Failure(FailureCode.INVALID, str(error))
        if not auth_file and not supplier_definition.accepts_direct_api_key and supplier is not SupplierKind.VERTEX:
            return Failure(FailureCode.INVALID, "supplier does not accept direct credentials")
        if operation_id is not None:
            existing: Final = await self._find_by_operation_id(operation_id)
            if existing is not None:
                return Success(to_view(existing))
        pool_settings: Final = await self._account_pool_settings()
        proxy_result: Final = await self._default_proxy(pool_settings)
        if isinstance(proxy_result, Failure):
            return proxy_result
        proxy_mode, proxy_profile_id, proxy_url = proxy_result.value
        now: Final = utc_now()
        try:
            identity: Final = credential_identity(credential_content, supplier.value, self._secrets)
        except CredentialConflict as error:
            return Failure(FailureCode.INVALID, str(error))
        record: Final = EnvironmentRecord(
            id=environment_id or uuid4(),
            version=0,
            desired_state=EnvironmentStatus.VALIDATING,
            operation_id=operation_id or str(uuid4()),
            name=name,
            provider=Provider.OPENAI,
            channel=ChannelKind.CLIPROXYAPI,
            supplier=supplier,
            authorization_flow=supplier_definition.authorization_flow
            if auth_file
            else AuthorizationFlow.DIRECT_CREDENTIAL,
            status=EnvironmentStatus.PROVISIONING,
            enabled=True,
            manual_cooldown=False,
            concurrency_limit=pool_settings.default_concurrency_limit,
            proxy_mode=proxy_mode,
            proxy_profile_id=proxy_profile_id,
            available_models=(),
            enabled_models=(),
            auth_file_name=None,
            auth_index=None,
            credential_fingerprints=identity.fingerprints,
            credential_email=identity.email,
            credential_account_id=identity.account_id,
            quota=QuotaSnapshot(),
            model_quotas=(),
            cooldown_until=None,
            oauth_state=None,
            oauth_expires_at=None,
            oauth_state_consumed_at=None,
            oauth_state_signature=None,
            oauth_provider_state=None,
            oauth_authorization_url=None,
            authorization_user_code=None,
            last_error=None,
            created_at=now,
            updated_at=now,
        )
        try:
            await self._ownership.claim(record.id, identity.fingerprints)
        except CredentialConflict as error:
            return Failure(FailureCode.CONFLICT, str(error))
        try:
            await self._repository.save(record)
        except Exception:
            await self._ownership.retain(record.id)
            return Failure(FailureCode.UPSTREAM, "credential card could not be persisted")
        try:
            channel: Final = self._channel(record)
            await channel.provision(record)
            await write_credential(channel, record, proxy_url)
            validating: Final = record.model_copy(
                update={
                    "version": record.version + 1,
                    "status": EnvironmentStatus.VALIDATING,
                    "desired_state": EnvironmentStatus.VALIDATING,
                    "updated_at": utc_now(),
                }
            )
            await self._repository.save(validating)
            observed: Final = await self.wait_for_direct_credential(channel, validating)
            desired: Final = configuration_from_record(observed, proxy_url).model_copy(
                update={"enabled_models": observed.enabled_models}
            )
            pending: Final = observed.model_copy(
                update={
                    "version": validating.version + 1,
                    "status": observed.status if auth_file else EnvironmentStatus.READY,
                    "desired_state": observed.status if auth_file else EnvironmentStatus.READY,
                    "configuration_pending": True,
                    "desired_configuration_version": validating.desired_configuration_version + 1,
                    "desired_configuration": desired,
                    "last_error": None,
                    "updated_at": utc_now(),
                }
            )
            await self._repository.save(pending)
            reconciled: Final = await self._apply_and_persist_configuration(pending, desired)
            if isinstance(reconciled, Failure):
                return reconciled
            return reconciled
        except Exception:
            current: Final = await self._repository.get(record.id) or record
            public_error: Final = RuntimeError("Direct credential validation failed")
            failed: Final = current.model_copy(
                update={
                    "version": current.version + 1,
                    "status": EnvironmentStatus.ERROR,
                    "desired_state": EnvironmentStatus.ERROR,
                    "configuration_pending": False,
                    "last_error": str(public_error),
                    "updated_at": utc_now(),
                }
            )
            await self._repository.save(failed)
            await self._log_event(failed, "authentication", public_error)
            return Failure(FailureCode.UPSTREAM, "direct credential validation failed")

    async def wait_for_direct_credential(
        self, channel: EnvironmentChannel, record: EnvironmentRecord
    ) -> EnvironmentRecord:
        deadline: Final = asyncio.get_running_loop().time() + self._direct_credential_validation_timeout_seconds
        last_error: Exception | None = None
        while asyncio.get_running_loop().time() < deadline:
            try:
                observed: Final = await channel.read_account(record)
                if observed.available_models and await channel.data_plane_health_check(observed):
                    return observed
            except Exception as error:
                last_error = error
            await asyncio.sleep(self._direct_credential_validation_interval_seconds)
        if last_error is not None:
            raise RuntimeError(_safe_error(last_error)) from last_error
        raise RuntimeError("credential was saved but no models became available")

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
        if any(
            item.proxy_profile_id is not None and proxy_url is None
            for item, proxy_url in zip(configuration.api_keys, proxy_urls)
        ):
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
        self, environment_id: UUID, request: OpenAICompatibleCredentialRequest
    ) -> Result[EnvironmentView]:
        return Failure(FailureCode.INVALID, "一张卡片只能使用一个凭证，请新建卡片，或删除旧卡片后重新创建")

    async def delete_openai_compatible_credential(
        self,
        environment_id: UUID,
        request: OpenAICompatibleCredentialDeleteRequest,
    ) -> Result[EnvironmentView]:
        """删除单张 OpenAI 兼容凭据，至少保留一张凭据以避免卡片失去路由身份。"""
        lock: Final = await self._lock_for(environment_id)
        async with lock, self._ownership.operation(environment_id):
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
                credential
                for index, credential in enumerate(configuration.credentials)
                if index != request.credential_index
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
                    update={
                        "status": EnvironmentStatus.ERROR,
                        "configuration_pending": False,
                        "last_error": _safe_error(error),
                        "updated_at": utc_now(),
                    }
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
