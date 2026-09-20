"""处理授权会话、回调和验证恢复，共用环境锁及凭据所有权。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets as token_secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID, uuid4

from account_pool.application.authorization import (
    _authorization_expires_at,
)
from account_pool.application.environments.contracts import (
    AUTHORIZATION_COMPLETE_STATUSES as _AUTHORIZATION_COMPLETE_STATUSES,
)
from account_pool.application.environments.contracts import (
    AUTHORIZATION_VALIDATION_TIMEOUT as _AUTHORIZATION_VALIDATION_TIMEOUT,
)
from account_pool.application.environments.contracts import HTTP_URL_ADAPTER as _HTTP_URL_ADAPTER
from account_pool.application.environments.contracts import (
    ApplyAndPersistConfigurationOperation,
    ChannelOperation,
    GatewayEnvironmentOperation,
    LockForOperation,
    LogEventOperation,
    StartAuthorizationOperation,
)
from account_pool.application.environments.contracts import AuthorizationConflict as _AuthorizationConflict
from account_pool.channels.registry import ChannelRegistry
from account_pool.config import Settings
from account_pool.credential_ownership import CredentialOwnership
from account_pool.domain import (
    AuthorizationFlow,
    AuthorizationView,
    CleanupProgress,
    EnvironmentRecord,
    EnvironmentStatus,
    EnvironmentView,
    OAuthCallback,
    configuration_from_record,
    to_view,
    utc_now,
)
from account_pool.ports import (
    EnvironmentRepository,
)
from account_pool.shared.error_safety import safe_error as _safe_error
from account_pool.shared.result import Failure, FailureCode, Result, Success
from account_pool.shared.secrets import EnvironmentSecretDeriver, SecretPurpose


@dataclass(frozen=True, slots=True)
class EnvironmentAuthorization:
    _apply_and_persist_configuration: ApplyAndPersistConfigurationOperation
    _channel: ChannelOperation
    _channels: ChannelRegistry
    _gateway_environment: GatewayEnvironmentOperation
    _lock_for: LockForOperation
    _log_event: LogEventOperation
    _ownership: CredentialOwnership
    _repository: EnvironmentRepository
    _secrets: EnvironmentSecretDeriver
    _settings: Settings
    _start_authorization: StartAuthorizationOperation

    async def authorize_environment(
        self,
        environment_id: UUID,
        operation_id: str | None = None,
    ) -> Result[AuthorizationView]:
        """为已有 Compose 环境创建新的、一次性的 OAuth state，不重建可复用资源。"""
        lock: Final = await self._lock_for(environment_id)
        async with lock, self._ownership.operation(environment_id):
            record: Final = await self._repository.get(environment_id)
            if record is None:
                return Failure(FailureCode.NOT_FOUND, "environment not found")
            if record.status is EnvironmentStatus.DELETING:
                return Failure(FailureCode.CONFLICT, "environment is being deleted")
            if record.authorization_flow is AuthorizationFlow.DIRECT_CREDENTIAL:
                return Failure(FailureCode.INVALID, "direct credential cards do not use OAuth authorization")
            if (
                operation_id is not None
                and record.operation_id == operation_id
                and record.oauth_authorization_url is not None
                and record.oauth_expires_at is not None
                and record.oauth_state_consumed_at is None
                and record.oauth_expires_at > utc_now()
            ):
                return Success(self.authorization_view(record))
            blocked: Final = record.model_copy(
                update={
                    "version": record.version + 1,
                    "status": EnvironmentStatus.AWAITING_AUTHORIZATION,
                    "desired_state": EnvironmentStatus.AWAITING_AUTHORIZATION,
                    "configuration_pending": False,
                    "auth_file_name": None,
                    "auth_index": None,
                    "credential_fingerprints": (),
                    "credential_email": None,
                    "credential_account_id": None,
                    "oauth_state": None,
                    "oauth_expires_at": None,
                    "oauth_state_consumed_at": None,
                    "oauth_state_signature": None,
                    "oauth_authorization_url": None,
                    "updated_at": utc_now(),
                }
            )
            claimed: Final = await self._repository.save_if_version(blocked, record.version)
            if claimed is None:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            return await self.authorize_prepared(claimed, operation_id)

    async def authorize_prepared(
        self, record: EnvironmentRecord, operation_id: str | None
    ) -> Result[AuthorizationView]:
        try:
            channel: Final = self._channel(record)
            await channel.ensure_control_plane_connections(record.id)
            result: Final = await self._start_authorization(record)
            provider_state, callback_state, callback_url, flow, user_code, expires_in = result
            _HTTP_URL_ADAPTER.validate_python(callback_url)
        except Exception as error:
            await self.persist_authorization_failure(record, str(error))
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
        return Success(self.authorization_view(saved))

    async def cancel_oauth_session(self, environment_id: UUID) -> Result[EnvironmentView]:
        lock: Final = await self._lock_for(environment_id)
        async with lock, self._ownership.operation(environment_id):
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
            if saved is None:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            return Success(to_view(saved))

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
        async with lock, self._ownership.operation(located.id):
            current: Final = await self._repository.find_by_oauth_state(callback.state)
            if current is None:
                return Failure(FailureCode.NOT_FOUND, "unknown or expired OAuth state")
            return await self.submit_oauth_callback_locked(callback, environment_id, current)

    async def submit_oauth_callback_locked(
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
        if record.oauth_state_signature is None or not self.valid_state_signature(record, callback.state):
            return Failure(FailureCode.CONFLICT, "invalid OAuth state")
        if record.oauth_state_consumed_at is None and (
            record.oauth_expires_at is None or record.oauth_expires_at <= utc_now()
        ):
            await self.persist_authorization_failure(record, "OAuth authorization expired")
            return Failure(FailureCode.CONFLICT, "OAuth authorization expired")
        consumed_at: Final = utc_now()
        consumed: Final = await self.consume_oauth_state(callback.state, consumed_at)
        if consumed is None:
            return Failure(FailureCode.CONFLICT, "OAuth callback has already been consumed")
        if environment_id is not None and consumed.id != environment_id:
            return Failure(FailureCode.CONFLICT, "OAuth state does not belong to this environment")
        if consumed.oauth_state_signature is None or not self.valid_state_signature(consumed, callback.state):
            return Failure(FailureCode.CONFLICT, "invalid OAuth state")
        if callback.code is None or not callback.code.strip():
            failure_reason: Final = (
                callback.error or callback.error_description or "OAuth authorization was not completed"
            )
            await self.persist_authorization_failure(consumed, failure_reason)
            return Failure(FailureCode.CONFLICT, "OAuth authorization was not completed")
        provider_state: Final = consumed.oauth_provider_state or callback.state
        provider_callback: Final = callback.model_copy(update={"state": provider_state})
        try:
            channel: Final = self._channel(consumed)
            await channel.submit_callback(consumed, provider_callback)
        except Exception as error:
            await self.persist_authorization_failure(consumed, str(error))
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
            validation: Final = await self.complete_authorization(claimed)
        except _AuthorizationConflict:
            return Failure(FailureCode.CONFLICT, "environment authorization is still being reconciled")
        if isinstance(validation, Failure):
            return validation
        validated: Final = validation.value
        if validated.status is EnvironmentStatus.READY and not self._gateway_environment(validated).routable:
            return Failure(FailureCode.CONFLICT, "environment authorization is still being reconciled")
        return Success(to_view(validated))

    async def find_by_operation_id(self, operation_id: str) -> EnvironmentRecord | None:
        return await self._repository.find_by_operation_id(operation_id)

    async def consume_oauth_state(self, state: str, consumed_at: datetime) -> EnvironmentRecord | None:
        return await self._repository.consume_oauth_state(state, consumed_at)

    async def persist_authorization_failure(
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

    def state_signature(self, environment_id: UUID, state: str) -> str:
        key: Final = self._secrets.derive(environment_id, SecretPurpose.OAUTH_STATE).encode("ascii")
        message: Final = f"{environment_id.hex}:{state}".encode()
        return hmac.new(key, message, hashlib.sha256).hexdigest()

    def callback_state(self, record: EnvironmentRecord) -> str:
        nonce: Final = token_secrets.token_urlsafe(32)
        signature: Final = self.state_signature(record.id, nonce)
        # state 本身不携带凭据，只使用随机值和环境绑定签名，防止跨环境转发与重放。
        return f"{nonce}.{signature}"

    def valid_state_signature(self, record: EnvironmentRecord, state: str) -> bool:
        nonce, separator, signature = state.rpartition(".")
        if not separator or not nonce or not signature:
            return False
        expected: Final = self.state_signature(record.id, nonce)
        return hmac.compare_digest(signature, expected) and (
            record.oauth_state_signature is None or hmac.compare_digest(record.oauth_state_signature, signature)
        )

    def authorization_view(self, record: EnvironmentRecord) -> AuthorizationView:
        if record.oauth_authorization_url is None or record.oauth_expires_at is None:
            raise RuntimeError("authorization operation has no active credentials")
        if record.authorization_flow is AuthorizationFlow.DIRECT_CREDENTIAL:
            raise RuntimeError("direct credential cards do not have authorization instructions")
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

    async def validate_authorized(self, record: EnvironmentRecord) -> EnvironmentRecord:
        started_at: Final = record.oauth_state_consumed_at or record.created_at
        remaining: Final = (started_at + _AUTHORIZATION_VALIDATION_TIMEOUT - utc_now()).total_seconds()
        if remaining <= 0:
            return await self.persist_validation_retry(record, "Account channel did not become ready")
        try:
            # 限制包含底层连接退避在内的总耗时，避免 SDK 重试长期占住环境锁。
            async with asyncio.timeout(min(15.0, remaining)):
                channel: Final = self._channel(record)
                observed: Final = await channel.read_account(record)
                healthy: Final = await channel.data_plane_health_check(observed)
        except Exception as error:
            return await self.persist_validation_retry(record, _safe_error(error))
        if not healthy:
            return await self.persist_validation_retry(record, "Account channel data plane validation failed")
        return observed

    async def persist_validation_retry(self, record: EnvironmentRecord, message: str) -> EnvironmentRecord:
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
                    if expired
                    else f"Waiting for account channel startup; retrying: {message}"
                ),
                "updated_at": now,
            }
        )
        saved: Final = await self._repository.save_if_version(updated, record.version)
        if saved is None:
            raise _AuthorizationConflict
        await self._log_event(saved, "validation", RuntimeError(message), retryable=not expired)
        return saved

    async def complete_authorization(self, record: EnvironmentRecord) -> Result[EnvironmentRecord]:
        validated: Final = await self.validate_authorized(record)
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

    async def persist_cleanup_progress(
        self,
        record: EnvironmentRecord,
        progress: CleanupProgress,
    ) -> EnvironmentRecord | None:
        updated: Final = record.model_copy(update={"cleanup_progress": progress, "updated_at": utc_now()})
        return await self._repository.save_if_version(updated, record.version)

    async def reloaded_consumed_state(
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

    async def update_authorization_error(self, record: EnvironmentRecord, message: str | None) -> EnvironmentRecord:
        if record.last_error == message:
            return record
        updated: Final = record.model_copy(
            update={"version": record.version + 1, "last_error": message, "updated_at": utc_now()}
        )
        saved: Final = await self._repository.save_if_version(updated, record.version)
        return saved or await self._repository.get(record.id) or record

    async def refresh_authorization(self, record: EnvironmentRecord) -> EnvironmentRecord:
        if record.oauth_state is None or record.oauth_expires_at is None:
            return record
        if record.oauth_expires_at <= utc_now():
            return await self.persist_authorization_failure(record, "OAuth authorization expired")
        if record.oauth_state_signature is None or not self.valid_state_signature(record, record.oauth_state):
            return await self.persist_authorization_failure(record, "invalid OAuth state")
        try:
            channel: Final = self._channel(record)
            status: Final = await channel.authorization_status(
                record, record.oauth_provider_state or record.oauth_state
            )
        except Exception as error:
            # 展示脱敏后的失败原因并保留授权状态，短暂断网或写入失败后仍可重试。
            return await self.update_authorization_error(record, _safe_error(error))
        if status == "wait":
            return await self.update_authorization_error(record, None)
        if status.startswith("error:"):
            return await self.persist_authorization_failure(record, status.removeprefix("error:"))
        if status != "ok":
            return record
        consumed_at: Final = utc_now()
        consumed_result: Final = await self.consume_oauth_state(record.oauth_state, consumed_at)
        consumed: Final = (
            consumed_result
            if consumed_result is not None
            else await self.reloaded_consumed_state(record, record.oauth_state)
        )
        if consumed is None:
            return await self._repository.get(record.id) or record
        if consumed.oauth_state_signature is None or not self.valid_state_signature(consumed, record.oauth_state):
            return await self.persist_authorization_failure(consumed, "invalid OAuth state")
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
            completion: Final = await self.complete_authorization(claimed)
        except _AuthorizationConflict:
            return await self._repository.get(record.id) or record
        if isinstance(completion, Failure):
            return await self._repository.get(claimed.id) or record
        return completion.value
