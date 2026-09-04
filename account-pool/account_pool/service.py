"""本模块编排环境创建、授权、配置更新和状态恢复，不直接处理 HTTP 细节。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets as token_secrets
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, TypeVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

from pydantic import HttpUrl, TypeAdapter

from account_pool.cleanup import compose_removed, directory_removed, routes_removed
from account_pool.config import Settings, validate_proxy_profile_url
from account_pool.domain import (
    AuthorizationView,
    CleanupProgress,
    CreateEnvironmentRequest,
    EnvironmentConfiguration,
    EnvironmentRecord,
    EnvironmentStatus,
    EnvironmentView,
    GatewayEnvironment,
    OAuthCallback,
    Provider,
    ProxyMode,
    ProxyProfile,
    QuotaSnapshot,
    UpdateEnvironmentRequest,
    configuration_from_record,
    to_view,
    utc_now,
)
from account_pool.error_safety import safe_error
from account_pool.ports import CLIProxyClient, EnvironmentRepository, EnvironmentRuntime, ProxyProfileRepository
from account_pool.result import Failure, FailureCode, Result, Success
from account_pool.secrets import EnvironmentSecretDeriver, SecretPurpose

T = TypeVar("T")
_HTTP_URL_ADAPTER: Final = TypeAdapter(HttpUrl)


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


class EnvironmentService:
    def __init__(
        self,
        settings: Settings,
        repository: EnvironmentRepository,
        runtime: EnvironmentRuntime,
        cli_proxy: CLIProxyClient,
        proxy_profiles: ProxyProfileRepository,
        secrets: EnvironmentSecretDeriver,
    ) -> None:
        self._settings: Final = settings
        self._repository: Final = repository
        self._runtime: Final = runtime
        self._cli_proxy: Final = cli_proxy
        self._proxy_profiles: Final = proxy_profiles
        self._secrets: Final = secrets
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._locks_guard: Final = asyncio.Lock()

    async def list_environments(self) -> tuple[EnvironmentView, ...]:
        records: Final = await self._repository.list()
        refreshed: Final = await asyncio.gather(*(self._refresh_if_needed(record) for record in records))
        return tuple(to_view(record) for record in refreshed)

    async def get_environment(self, environment_id: UUID) -> Result[EnvironmentView]:
        record: Final = await self._repository.get(environment_id)
        if record is None:
            return Failure(FailureCode.NOT_FOUND, "environment not found")
        refreshed: Final = await self._refresh_if_needed(record)
        return Success(to_view(refreshed))

    async def list_proxy_profiles(self) -> tuple[ProxyProfile, ...]:
        return await self._proxy_profiles.list()

    async def list_gateway_environments(self) -> tuple[GatewayEnvironment, ...]:
        records: Final = await self._repository.list()
        refreshed: Final = await asyncio.gather(*(self._refresh_if_needed(record) for record in records))
        return tuple(self._gateway_environment(record) for record in refreshed)

    async def _start_authorization(
        self,
        record: EnvironmentRecord,
    ) -> tuple[str, str, str]:
        authorization_url, provider_state = await self._cli_proxy.start_openai_authorization(record)
        callback_state: Final = self._callback_state(record)
        callback_url: Final = _replace_state(authorization_url, callback_state)
        return provider_state, callback_state, callback_url

    async def create_environment(self, request: CreateEnvironmentRequest) -> Result[AuthorizationView]:
        if request.operation_id is not None:
            existing: Final = await self._find_by_operation_id(request.operation_id)
            if existing is not None:
                if existing.oauth_authorization_url is not None and existing.oauth_expires_at is not None:
                    return Success(self._authorization_view(existing))
                return Failure(FailureCode.CONFLICT, "environment operation is still in progress")
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
            status=EnvironmentStatus.PROVISIONING,
            enabled=True,
            manual_cooldown=False,
            concurrency_limit=1,
            proxy_mode=ProxyMode.DEFAULT_GATEWAY,
            proxy_profile_id=None,
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
            await self._runtime.provision(record)
            provider_state, callback_state, callback_url = await self._start_authorization(record)
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
            return Failure(FailureCode.UPSTREAM, "environment provisioning failed")
        expires_at: Final = utc_now() + timedelta(minutes=5)
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
                "updated_at": utc_now(),
            }
        )
        await self._repository.save(awaiting)
        command: Final = (
            f"ssh -N -L 1455:127.0.0.1:{self._settings.callback_port} "
            f"{self._settings.ssh_user}@{self._settings.ssh_host}"
        )
        return Success(
            AuthorizationView(
                flow=awaiting.authorization_flow,
                authorization_url=validated_authorization_url,
                ssh_command=command,
                user_code=awaiting.authorization_user_code,
                expires_at=expires_at,
            )
        )

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
                await self._runtime.ensure_control_plane_connections(record.id)
                provider_state, callback_state, callback_url = await self._start_authorization(record)
                _HTTP_URL_ADAPTER.validate_python(callback_url)
            except Exception as error:
                await self._persist_authorization_failure(record, str(error))
                return Failure(FailureCode.UPSTREAM, "environment authorization failed")
            expires_at: Final = utc_now() + timedelta(minutes=5)
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
                    "last_error": None,
                    "updated_at": utc_now(),
                }
            )
            saved: Final = await self._repository.save_if_version(authorized, record.version)
            if saved is None:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            return Success(self._authorization_view(saved))

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
            await self._cli_proxy.submit_callback(consumed, provider_callback)
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
        return AuthorizationView(
            flow=record.authorization_flow,
            authorization_url=_HTTP_URL_ADAPTER.validate_python(record.oauth_authorization_url),
            ssh_command=(
                f"ssh -N -L 1455:127.0.0.1:{self._settings.callback_port} "
                f"{self._settings.ssh_user}@{self._settings.ssh_host}"
            ),
            user_code=record.authorization_user_code,
            expires_at=record.oauth_expires_at,
        )

    async def _validate_authorized(self, record: EnvironmentRecord) -> EnvironmentRecord:
        try:
            observed: Final = await self._cli_proxy.read_account(record)
        except Exception as error:
            failed: Final = record.model_copy(
                update={
                    "status": EnvironmentStatus.ERROR,
                    "desired_state": EnvironmentStatus.ERROR,
                    "last_error": _safe_error(error),
                    "updated_at": utc_now(),
                }
            )
            return await self._repository.save_if_version(failed, record.version) or failed
        if not await self._cli_proxy.data_plane_health_check(observed):
            failed_health: Final = observed.model_copy(
                update={
                    "status": EnvironmentStatus.ERROR,
                    "desired_state": EnvironmentStatus.ERROR,
                    "last_error": "CLIProxyAPI data plane validation failed",
                    "updated_at": utc_now(),
                }
            )
            return await self._repository.save_if_version(failed_health, record.version) or failed_health
        return observed

    async def _complete_authorization(self, record: EnvironmentRecord) -> Result[EnvironmentRecord]:
        validated: Final = await self._validate_authorized(record)
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
        desired: Final = completed.desired_configuration or configuration_from_record(completed)
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
            if record.auth_file_name is None:
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
            await self._cli_proxy.apply_configuration(record, desired)
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
                return Failure(FailureCode.UPSTREAM, "environment metadata cleanup failed")
            return Success(None)

    async def _remove_compose_step(self, record: EnvironmentRecord) -> EnvironmentRecord | None:
        if record.cleanup_progress.compose_removed:
            return record
        if hasattr(self._runtime, "remove_compose"):
            await self._runtime.remove_compose(record)
        else:
            await self._runtime.remove(record)
        return await self._persist_cleanup_progress(record, compose_removed(record.cleanup_progress))

    async def _remove_directory_step(self, record: EnvironmentRecord) -> EnvironmentRecord | None:
        if record.cleanup_progress.directory_removed:
            return record
        if hasattr(self._runtime, "remove_directory"):
            await self._runtime.remove_directory(record.id)
        return await self._persist_cleanup_progress(record, directory_removed())

    async def _automatic_cooldown_before_update(
        self,
        record: EnvironmentRecord,
        manual_cooldown: bool,
    ) -> _AutomaticCooldownState:
        # 自动冷却必须先通过真实数据面探活，配置保存不能成为绕过额度保护的入口。
        if manual_cooldown:
            return _AutomaticCooldownState.ACTIVE if record.cooldown_until is not None else _AutomaticCooldownState.NONE
        if record.cooldown_until is not None:
            if record.cooldown_until > utc_now():
                return _AutomaticCooldownState.ACTIVE
            return (
                _AutomaticCooldownState.RECOVERED
                if await self._cli_proxy.data_plane_health_check(record)
                else _AutomaticCooldownState.BLOCKED
            )
        if record.manual_cooldown:
            return (
                _AutomaticCooldownState.RECOVERED
                if await self._cli_proxy.data_plane_health_check(record)
                else _AutomaticCooldownState.BLOCKED
            )
        return (
            _AutomaticCooldownState.ACTIVE
            if record.status == EnvironmentStatus.COOLING_DOWN
            else _AutomaticCooldownState.NONE
        )

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
                return current if isinstance(completion, Failure) else completion.value
            if current.auth_file_name is None:
                return current
            if current.automatic_cooldown and not await self._cli_proxy.data_plane_health_check(current):
                return current
            if _cooldown_active(current):
                return current
            if _cooldown_elapsed(current) and not await self._cli_proxy.data_plane_health_check(current):
                return current
            try:
                observed: Final = await self._cli_proxy.read_account(current)
            except Exception:
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

    async def _refresh_authorization(self, record: EnvironmentRecord) -> EnvironmentRecord:
        if record.oauth_state is None or record.oauth_expires_at is None:
            return record
        if record.oauth_expires_at <= utc_now():
            return await self._persist_authorization_failure(record, "OAuth authorization expired")
        if record.oauth_state_signature is None or not self._valid_state_signature(record, record.oauth_state):
            return await self._persist_authorization_failure(record, "invalid OAuth state")
        try:
            status: Final = await self._cli_proxy.authorization_status(
                record,
                record.oauth_provider_state or record.oauth_state,
            )
        except Exception:
            return record
        if status == "wait":
            return record
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
            if completion.code is FailureCode.CONFLICT:
                return await self._repository.get(claimed.id) or record
            return record
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
        routable: Final = (
            record.status == EnvironmentStatus.READY
            and record.enabled
            and not record.manual_cooldown
            and record.cooldown_until is None
            and not record.configuration_pending
            and record.desired_configuration_version <= record.observed_configuration_version
        )
        return GatewayEnvironment(
            id=record.id,
            routable=routable,
            concurrency_limit=record.concurrency_limit,
            enabled_models=record.enabled_models,
            api_base=f"http://cliproxy-{record.id.hex}:8317/v1",
            api_key=self._secrets.derive(record.id, SecretPurpose.GATEWAY),
        )

    async def _lock_for(self, environment_id: UUID) -> asyncio.Lock:
        async with self._locks_guard:
            existing: Final = self._locks.get(environment_id)
            if existing is not None:
                return existing
            created: Final = asyncio.Lock()
            self._locks[environment_id] = created
            return created


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
