"""处理授权会话、回调和验证恢复，共用环境锁及凭据所有权。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets as token_secrets
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID, uuid4

from account_pool.application.authorization import (
    _authorization_expires_at,
    saved_login_can_replace,
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
from account_pool.channels.cliproxyapi.channel import CLIProxyAPIChannel
from account_pool.channels.cliproxyapi.protocol import StagedOAuthProbeFailure
from account_pool.channels.registry import ChannelRegistry
from account_pool.config import Settings
from account_pool.credential_ownership import CredentialConflict, CredentialOwnership, oauth_account_digest
from account_pool.domain import (
    AuthorizationFlow,
    AuthorizationView,
    CleanupProgress,
    EnvironmentRecord,
    EnvironmentStatus,
    EnvironmentView,
    OAuthCallback,
    SupplierKind,
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
    _proxy_operation: Callable[[str | None], AbstractAsyncContextManager[None]]
    _repository: EnvironmentRepository
    _secrets: EnvironmentSecretDeriver
    _settings: Settings
    _start_authorization: StartAuthorizationOperation

    def _authorization_routable(self, record: EnvironmentRecord) -> bool:
        # 授权落盘可先完成；自动化占用仍由公共网关拦截，等待账号与推理验收。
        return self._gateway_environment(record.model_copy(update={
            "oauth_automation_operation": None, "oauth_verification_pending": False,
        })).routable

    async def authorize_environment(
        self,
        environment_id: UUID,
        operation_id: str | None = None,
        *,
        require_empty: bool = False,
        reauthorize_email: str | None = None,
        expected_account: str | None = None,
        verification_model: str | None = None,
    ) -> Result[AuthorizationView]:
        """为已有 Compose 环境创建新的、一次性的 OAuth state，不重建可复用资源。"""
        lock: Final = await self._lock_for(environment_id)
        async with lock, self._ownership.operation(environment_id):
            record: Final = await self._repository.get(environment_id)
            if record is None:
                return Failure(FailureCode.NOT_FOUND, "environment not found")
            if record.oauth_automation_operation is not None and record.oauth_automation_operation != operation_id:
                return Failure(FailureCode.CONFLICT, "卡片正由授权自动化独占，请先取消自动化任务")
            if record.status is EnvironmentStatus.DELETING:
                return Failure(FailureCode.CONFLICT, "environment is being deleted")
            if require_empty and (not record.enabled or record.manual_cooldown):
                return Failure(FailureCode.CONFLICT, "卡片停用或手动冷却，不启动登录资料授权")
            if require_empty and (record.auth_file_name or record.credential_fingerprints) and (
                reauthorize_email is None
                or operation_id is None or record.oauth_automation_operation != operation_id
                or not saved_login_can_replace(record, reauthorize_email, utc_now())
            ):
                return Failure(FailureCode.CONFLICT, "卡片已有凭据，仅允许同账号的失效凭据重新授权")
            if record.authorization_flow is AuthorizationFlow.DIRECT_CREDENTIAL:
                return Failure(FailureCode.INVALID, "direct credential cards do not use OAuth authorization")
            if record.credential_refresh.save_pending:
                return Failure(FailureCode.CONFLICT, "新刷新凭据尚未确认保存，不能覆盖")
            if (
                operation_id is not None
                and record.operation_id == operation_id
                and record.oauth_authorization_url is not None
                and record.oauth_expires_at is not None
                and record.oauth_state_consumed_at is None
                and record.oauth_expires_at > utc_now()
            ):
                return Success(self.authorization_view(record))
            fallback: Final = record.access_token_fallback
            now: Final = utc_now()
            # 在锁内保护已经验证的降级凭据，避免旧页面请求清掉仍可用的绑定。
            if record.email_rebinding_job_id is None and any(fallback.usable_for(model, now) for model in record.enabled_models):
                return Failure(FailureCode.CONFLICT, "访问令牌降级仍可用，未启动重新授权")
            if record.oauth_staged and record.oauth_provider_state and record.oauth_state_consumed_at:
                return Failure(FailureCode.CONFLICT, "已有回调结果待核对，请先恢复原卡授权")
            codex: Final = record.supplier is SupplierKind.OPENAI_CODEX
            account: Final = expected_account or (
                oauth_account_digest(record.credential_email, self._secrets) if codex and record.credential_email else None
            )
            blocked: Final = record.model_copy(
                update={
                    "version": record.version + 1,
                    "status": EnvironmentStatus.AWAITING_AUTHORIZATION,
                    "desired_state": EnvironmentStatus.AWAITING_AUTHORIZATION,
                    "configuration_pending": False,
                    **({} if codex else {
                        "auth_file_name": None, "auth_index": None, "credential_fingerprints": (),
                        "credential_email": None, "credential_account_id": None,
                    }),
                    "oauth_previous_status": (record.oauth_previous_status or record.status) if codex else None,
                    "oauth_expected_account": account,
                    "oauth_requested_model": verification_model,
                    **({"credential_refresh": record.credential_refresh.model_copy(update={
                        "schedule_reason": "reauthorization_required",
                    })} if codex and record.last_error == "上游认证失效，请重新认证" else {}),
                    "oauth_state": None,
                    "oauth_expires_at": None,
                    "oauth_state_consumed_at": None,
                    "oauth_state_signature": None,
                    "oauth_authorization_url": None,
                    "updated_at": utc_now(),
                }
            )
            async with self._proxy_operation(record.proxy_profile_id):
                claimed: Final = await self._repository.save_if_version(blocked, record.version)
                if claimed is None:
                    return Failure(FailureCode.CONFLICT, "environment was changed by another request")
                return await self._authorize_prepared_locked(claimed, operation_id)

    async def authorize_prepared(
        self, record: EnvironmentRecord, operation_id: str | None
    ) -> Result[AuthorizationView]:
        async with self._proxy_operation(record.proxy_profile_id):
            return await self._authorize_prepared_locked(record, operation_id)

    async def _authorize_prepared_locked(
        self, record: EnvironmentRecord, operation_id: str | None
    ) -> Result[AuthorizationView]:
        if record.oauth_automation_operation is not None and record.oauth_automation_operation != operation_id:
            return Failure(FailureCode.CONFLICT, "卡片正由授权自动化独占，请先取消自动化任务")
        try:
            channel: Final = self._channel(record)
            await channel.ensure_control_plane_connections(record.id)
            result: Final = await self._start_authorization(record)
            provider_state, callback_state, callback_url, flow, user_code, expires_in, staged = result
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
                "oauth_staged": staged,
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

    async def cancel_oauth_session(self, environment_id: UUID, operation_id: str | None = None) -> Result[EnvironmentView]:
        lock: Final = await self._lock_for(environment_id)
        async with lock, self._ownership.operation(environment_id):
            record: Final = await self._repository.get(environment_id)
            if record is None:
                return Failure(FailureCode.NOT_FOUND, "environment not found")
            if record.oauth_automation_operation is not None and record.oauth_automation_operation != operation_id:
                return Failure(FailureCode.CONFLICT, "请从自动化任务取消，不中断其他任务的授权")
            state: Final = record.oauth_provider_state or record.oauth_state
            if record.status is not EnvironmentStatus.AWAITING_AUTHORIZATION or state is None:
                return Failure(FailureCode.CONFLICT, "OAuth authorization is not pending")
            try:
                await self._channel(record).cancel_oauth_session(record, state)
            except Exception as error:
                await self._log_event(record, "authorization", error)
                return Failure(FailureCode.UPSTREAM, "OAuth session cancellation failed")
            restored_status: Final = (
                (record.oauth_previous_status or EnvironmentStatus.READY)
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
                    "oauth_staged": False,
                    "oauth_expected_account": None,
                    "oauth_requested_model": None,
                    "oauth_previous_status": None,
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
            if not consumed.oauth_staged:
                await self.persist_authorization_failure(consumed, str(error))
                return Failure(FailureCode.UPSTREAM, "OAuth callback failed")
            # 已消费，通知 CPA 的请求结果未知；只查询暂存结果，不重新转发授权码。
            await self._log_event(consumed, "authorization", RuntimeError("OAuth callback result is unconfirmed"))
        claimed: Final = await self._begin_validation(consumed)
        if claimed is None:
            return Failure(FailureCode.CONFLICT, "environment was changed by another request")
        if claimed.oauth_staged:
            return Success(to_view(claimed))
        try:
            validation: Final = await self.complete_authorization(claimed)
        except _AuthorizationConflict:
            return Failure(FailureCode.CONFLICT, "environment authorization is still being reconciled")
        if isinstance(validation, Failure):
            return validation
        validated: Final = validation.value
        if validated.status is EnvironmentStatus.READY and not self._authorization_routable(validated):
            return Failure(FailureCode.CONFLICT, "environment authorization is still being reconciled")
        return Success(to_view(validated))

    async def _begin_validation(self, consumed: EnvironmentRecord) -> EnvironmentRecord | None:
        validating: Final = consumed.model_copy(
            update={
                "version": consumed.version + 1,
                "status": EnvironmentStatus.VALIDATING,
                "desired_state": EnvironmentStatus.VALIDATING,
                "oauth_expires_at": None,
                "oauth_provider_state": consumed.oauth_provider_state if consumed.oauth_staged else None,
                "oauth_authorization_url": None,
                "last_error": None,
                "updated_at": utc_now(),
            }
        )
        return await self._repository.save_if_version(validating, consumed.version)

    async def find_by_operation_id(self, operation_id: str) -> EnvironmentRecord | None:
        return await self._repository.find_by_operation_id(operation_id)

    async def consume_oauth_state(self, state: str, consumed_at: datetime) -> EnvironmentRecord | None:
        return await self._repository.consume_oauth_state(state, consumed_at)

    async def persist_authorization_failure(
        self,
        record: EnvironmentRecord,
        message: str,
    ) -> EnvironmentRecord:
        status: Final = (
            record.oauth_previous_status if record.auth_file_name and record.oauth_previous_status in {
                EnvironmentStatus.READY, EnvironmentStatus.COOLING_DOWN, EnvironmentStatus.ERROR,
            } else EnvironmentStatus.ERROR
        )
        failed: Final = record.model_copy(
            update={
                "version": record.version + 1,
                "status": status,
                "desired_state": status,
                "last_error": _safe_error(RuntimeError(message)),
                "oauth_state": None,
                "oauth_expires_at": None,
                "oauth_state_signature": None,
                "oauth_provider_state": None,
                "oauth_staged": False,
                "oauth_expected_account": None,
                "oauth_requested_model": None,
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

    async def validate_authorized(
        self, record: EnvironmentRecord, *, recovery_content: bytes | None = None,
    ) -> EnvironmentRecord:
        started_at: Final = record.oauth_state_consumed_at or record.created_at
        remaining: Final = (started_at + _AUTHORIZATION_VALIDATION_TIMEOUT - utc_now()).total_seconds()
        if remaining <= 0 and recovery_content is None:
            return await self.persist_validation_retry(record, "Account channel did not become ready")
        if record.oauth_staged and record.oauth_provider_state:
            staged: Final = await self._activate_codex_stage(record, recovery_content)
            if staged is not None:
                return staged
        try:
            # 限制包含底层连接退避在内的总耗时，避免 SDK 重试长期占住环境锁。
            async with asyncio.timeout(15.0 if record.oauth_staged else min(15.0, remaining)):
                channel: Final = self._channel(record)
                observed: Final = await channel.read_account(record)
                healthy: Final = await channel.data_plane_health_check(observed)
        except Exception as error:
            return await self.persist_validation_retry(record, _safe_error(error))
        if not healthy:
            return await self.persist_validation_retry(record, "Account channel data plane validation failed")
        return observed

    async def _activate_codex_stage(
        self, record: EnvironmentRecord, content: bytes | None,
    ) -> EnvironmentRecord | None:
        channel: Final = self._channels.channel(record.channel)
        if not isinstance(channel, CLIProxyAPIChannel):
            return await self._stage_failure(record, "卡片运行时不支持暂存授权")
        try:
            async with asyncio.timeout(8):
                candidate: Final = content if content is not None else await channel.staged_oauth_content(record)
                status: Final = (
                    await channel.authorization_status(record, record.oauth_provider_state or "")
                    if candidate is None else None
                )
            if candidate is None:
                if status and status.startswith("error:"):
                    return await self._stage_failure(record, _safe_error(RuntimeError(status.removeprefix("error:"))))
                return await self.persist_validation_retry(record, "等待 CPA 保存暂存凭据")
            async with asyncio.timeout(53):
                await channel.activate_staged_oauth(record, candidate)
        except CredentialConflict as error:
            # 尚未发布候选凭据。确认屏蔽旧会话后，允许在原卡修正资料再试。
            try:
                async with asyncio.timeout(8):
                    await channel.cancel_oauth_session(record, record.oauth_provider_state or "")
                    await self._ownership.retain(record.id, record.credential_fingerprints)
            except Exception:
                return await self._stage_failure(record, "凭据归属冲突，等待清理暂存文件")
            return await self._stage_failure(record, str(error), discarded=True)
        except StagedOAuthProbeFailure as error:
            if error.reason == "authentication_failed":
                try:
                    async with asyncio.timeout(10):
                        safe: Final = await self._fence_unchanged_oauth(record, channel)
                except Exception:
                    return await self._stage_failure(record, "候选凭据被拒绝，暂不能确认旧会话已停止")
                return await self._stage_failure(
                    record, "候选凭据被拒绝，已隔离，可以原卡重新授权" if safe else "凭据状态已改变，请先核对",
                    discarded=safe,
                )
            return await self._stage_failure(record, str(error))
        except Exception:
            return await self._stage_failure(record, "CPA 尚未确认暂存凭据验收和切换，请在原卡核对后重试")
        return None

    async def _stage_failure(
        self, record: EnvironmentRecord, message: str, *, discarded: bool = False,
    ) -> EnvironmentRecord:
        failed: Final = record.model_copy(update={
            "version": record.version + 1, "status": EnvironmentStatus.ERROR,
            "desired_state": EnvironmentStatus.ERROR, "last_error": message, "updated_at": utc_now(),
            **({"oauth_provider_state": None} if discarded else {}),
        })
        saved: Final = await self._repository.save_if_version(failed, record.version)
        if saved is None:
            raise _AuthorizationConflict
        await self._log_event(saved, "validation", RuntimeError(message), retryable=True)
        return saved

    async def _fence_unchanged_oauth(self, record: EnvironmentRecord, channel: CLIProxyAPIChannel) -> bool:
        if not await channel.oauth_runtime_matches(record):
            return False
        if record.oauth_provider_state is not None:
            await channel.cancel_oauth_session(record, record.oauth_provider_state)
            if not await channel.oauth_runtime_matches(record):
                return False
        await self._ownership.retain(record.id, record.credential_fingerprints)
        return True

    async def recover_oauth_for_retry(
        self, record: EnvironmentRecord, model: str | None = None,
    ) -> Result[EnvironmentRecord]:
        """调用者持同卡凭据锁；只对账并隔离旧会话，不再次提交回调。"""
        channel: Final = self._channels.channel(record.channel)
        if not isinstance(channel, CLIProxyAPIChannel):
            return Failure(FailureCode.CONFLICT, "卡片运行时无法核对 OAuth 结果")
        try:
            async with asyncio.timeout(10):
                candidate: Final = await channel.staged_oauth_content(record) if record.oauth_staged else None
                if candidate is None and not await self._fence_unchanged_oauth(record, channel):
                    return Failure(FailureCode.CONFLICT, "运行时存在未确认凭据，未再次登录或覆盖")
            if candidate is not None:
                selected: Final = record.model_copy(update={"oauth_requested_model": model or record.oauth_requested_model})
                return await self.complete_authorization(selected, recovery_content=candidate)
            updated: Final = record.model_copy(update={
                "version": record.version + 1, "oauth_provider_state": None, "oauth_staged": False,
                "oauth_state": None, "oauth_expires_at": None, "oauth_state_signature": None,
                "oauth_state_consumed_at": None, "oauth_authorization_url": None, "updated_at": utc_now(),
            })
            saved: Final = await self._repository.save_if_version(updated, record.version)
            return Success(saved) if saved is not None else Failure(FailureCode.CONFLICT, "卡片状态已改变，请刷新后重试")
        except Exception:
            return Failure(FailureCode.CONFLICT, "无法确认旧会话或凭据状态，未再次登录")

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

    async def complete_authorization(
        self, record: EnvironmentRecord, *, recovery_content: bytes | None = None,
    ) -> Result[EnvironmentRecord]:
        validated: Final = await self.validate_authorized(record, recovery_content=recovery_content)
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
                "oauth_staged": False,
                "oauth_expected_account": None,
                "oauth_requested_model": None,
                "oauth_previous_status": None,
                "oauth_verification_pending": record.supplier is SupplierKind.OPENAI_CODEX
                and (record.oauth_automation_operation is not None or recovery_content is not None),
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
            await self._discard_confirmed_stage(record)
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
        await self._discard_confirmed_stage(record)
        reconciled: Final = await self._apply_and_persist_configuration(claimed, desired)
        if isinstance(reconciled, Failure):
            return reconciled
        persisted: Final = await self._repository.get(claimed.id)
        if persisted is None or persisted.status not in _AUTHORIZATION_COMPLETE_STATUSES:
            raise _AuthorizationConflict
        if persisted.status is EnvironmentStatus.READY and not self._authorization_routable(persisted):
            raise _AuthorizationConflict
        return Success(persisted)

    async def _discard_confirmed_stage(self, record: EnvironmentRecord) -> None:
        if record.oauth_staged and record.oauth_provider_state:
            try:
                async with asyncio.timeout(5):
                    await self._channel(record).cancel_oauth_session(record, record.oauth_provider_state)
            except Exception:
                await self._log_event(record, "cleanup", RuntimeError("OAuth 暂存文件清理失败"), retryable=True)

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
        if record.oauth_staged and record.oauth_state_consumed_at is not None:
            resuming: Final = await self._begin_validation(record)
            return resuming or await self._repository.get(record.id) or record
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
        claimed: Final = await self._begin_validation(consumed)
        if claimed is None:
            return await self._repository.get(record.id) or record
        try:
            completion: Final = await self.complete_authorization(claimed)
        except _AuthorizationConflict:
            return await self._repository.get(record.id) or record
        if isinstance(completion, Failure):
            return await self._repository.get(claimed.id) or record
        return completion.value
