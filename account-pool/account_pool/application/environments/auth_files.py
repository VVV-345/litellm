"""编排认证文件上传与替换，共用凭据所有权、环境锁和配置恢复。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from account_pool.application.environments.contracts import (
    AUTHORIZATION_COMPLETE_STATUSES as _AUTHORIZATION_COMPLETE_STATUSES,
)
from account_pool.application.environments.contracts import (
    ApplyAndPersistConfigurationOperation,
    ChannelOperation,
    GatewayEnvironmentOperation,
    LockForOperation,
    LogEventOperation,
    RefreshIfNeededOperation,
    WaitForDirectCredentialOperation,
)
from account_pool.credential_ownership import CredentialConflict, CredentialOwnership, credential_identity
from account_pool.domain import (
    ChannelKind,
    EnvironmentRecord,
    EnvironmentStatus,
    EnvironmentView,
    QuotaSnapshot,
    configuration_from_record,
    to_view,
    utc_now,
)
from account_pool.ports import (
    CLIProxyClient,
    EnvironmentRepository,
)
from account_pool.shared.error_safety import safe_error as _safe_error
from account_pool.shared.result import Failure, FailureCode, Result, Success
from account_pool.shared.secrets import EnvironmentSecretDeriver


@dataclass(frozen=True, slots=True)
class EnvironmentAuthFiles:
    _apply_and_persist_configuration: ApplyAndPersistConfigurationOperation
    _channel: ChannelOperation
    _cli_proxy: CLIProxyClient
    _gateway_environment: GatewayEnvironmentOperation
    _lock_for: LockForOperation
    _log_event: LogEventOperation
    _ownership: CredentialOwnership
    _refresh_if_needed: RefreshIfNeededOperation
    _repository: EnvironmentRepository
    _secrets: EnvironmentSecretDeriver
    _wait_for_direct_credential: WaitForDirectCredentialOperation

    async def upload_auth_file(
        self, environment_id: UUID, filename: str, content: bytes, content_type: str | None, *, replace: bool = False
    ) -> Result[EnvironmentView]:
        lock: Final = await self._lock_for(environment_id)
        async with lock, self._ownership.operation(environment_id):
            record: Final = await self._repository.get(environment_id)
            if record is None:
                return Failure(FailureCode.NOT_FOUND, "environment not found")
            if record.channel is not ChannelKind.CLIPROXYAPI:
                return Failure(FailureCode.INVALID, "auth files are supported by CLIProxyAPI cards only")
            if record.status is EnvironmentStatus.DELETING:
                return Failure(FailureCode.CONFLICT, "environment is being deleted")
            if not replace and (record.auth_file_name is not None or record.credential_fingerprints):
                return Failure(
                    FailureCode.CONFLICT, "该卡片已有凭证，一张卡片只能绑定一个凭证。如需替换，请使用“更换文件”"
                )
            try:
                identity: Final = credential_identity(content, record.supplier.value, self._secrets)
                await self._ownership.claim(record.id, identity.fingerprints)
            except CredentialConflict as error:
                return Failure(FailureCode.CONFLICT, str(error))
            blocked: Final = record.model_copy(
                update={
                    "version": record.version + 1,
                    "status": EnvironmentStatus.AWAITING_AUTHORIZATION,
                    "desired_state": EnvironmentStatus.AWAITING_AUTHORIZATION,
                    "configuration_pending": False,
                    "oauth_state": None,
                    "oauth_provider_state": None,
                    "oauth_expires_at": None,
                    "oauth_state_signature": None,
                    "oauth_state_consumed_at": None,
                    "oauth_authorization_url": None,
                    "authorization_user_code": None,
                    "updated_at": utc_now(),
                }
            )
            claimed_upload: Final = await self._repository.save_if_version(blocked, record.version)
            if claimed_upload is None:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            return await self.upload_auth_file_locked(
                claimed_upload, filename, content, content_type, record.oauth_provider_state or record.oauth_state
            )

    async def upload_auth_file_locked(
        self,
        record: EnvironmentRecord,
        filename: str,
        content: bytes,
        content_type: str | None,
        pending_state: str | None,
    ) -> Result[EnvironmentView]:
        channel: Final = self._channel(record)
        try:
            if pending_state is not None:
                await channel.cancel_oauth_session(record, pending_state)
            await self._cli_proxy.upload_auth_file(record, filename, content, content_type)
            identity: Final = credential_identity(content, record.supplier.value, self._secrets)
            uploaded: Final = record.model_copy(
                update={
                    "auth_file_name": filename,
                    "credential_fingerprints": identity.fingerprints,
                    "credential_email": identity.email,
                    "credential_account_id": identity.account_id,
                }
            )
            observed: Final = await self._wait_for_direct_credential(channel, uploaded)
        except Exception as error:
            failed: Final = record.model_copy(
                update={
                    "version": record.version + 1,
                    "status": EnvironmentStatus.ERROR,
                    "desired_state": EnvironmentStatus.ERROR,
                    "auth_file_name": filename,
                    "credential_fingerprints": (),
                    "last_error": "认证文件替换或验证失败，卡片已停止接单，请重试上传或删除",
                    "updated_at": utc_now(),
                }
            )
            await self._repository.save_if_version(failed, record.version)
            await self._log_event(record, "authentication", error)
            return Failure(FailureCode.UPSTREAM, f"auth file validation failed: {_safe_error(error)}")
        completed: Final = observed.model_copy(
            update={
                "version": record.version + 1,
                "desired_state": observed.status,
                "oauth_state": None,
                "oauth_expires_at": None,
                "oauth_state_consumed_at": None,
                "oauth_state_signature": None,
                "oauth_provider_state": None,
                "oauth_authorization_url": None,
                "authorization_user_code": None,
                "last_error": None,
                "updated_at": utc_now(),
            }
        )
        initial_desired: Final = completed.desired_configuration or configuration_from_record(completed)
        desired: Final = initial_desired.model_copy(update={"enabled_models": completed.enabled_models})
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
            return Failure(FailureCode.CONFLICT, "environment was changed by another request")
        reconciled: Final = await self._apply_and_persist_configuration(claimed, desired)
        if isinstance(reconciled, Failure):
            return reconciled
        persisted: Final = await self._repository.get(record.id)
        if persisted is None or persisted.status not in _AUTHORIZATION_COMPLETE_STATUSES:
            return Failure(FailureCode.CONFLICT, "auth file validation did not reach a usable state")
        if persisted.status is EnvironmentStatus.READY and not self._gateway_environment(persisted).routable:
            return Failure(FailureCode.CONFLICT, "auth file validation is still being reconciled")
        await self._ownership.retain(record.id, persisted.credential_fingerprints)
        await self._log_event(persisted, "authentication", None)
        return Success(to_view(persisted))

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
        lock: Final = await self._lock_for(environment_id)
        async with lock, self._ownership.operation(environment_id):
            record: Final = await self._repository.get(environment_id)
            if record is None:
                return Failure(FailureCode.NOT_FOUND, "environment not found")
            if record.channel is not ChannelKind.CLIPROXYAPI:
                return Failure(FailureCode.INVALID, "this card has no auth file")
            if record.status is EnvironmentStatus.DELETING:
                return Failure(FailureCode.CONFLICT, "environment is being deleted")
            blocked: Final = record.model_copy(
                update={
                    "version": record.version + 1,
                    "status": EnvironmentStatus.AWAITING_AUTHORIZATION,
                    "desired_state": EnvironmentStatus.AWAITING_AUTHORIZATION,
                    "configuration_pending": False,
                    "oauth_state": None,
                    "oauth_provider_state": None,
                    "oauth_expires_at": None,
                    "oauth_state_signature": None,
                    "oauth_state_consumed_at": None,
                    "oauth_authorization_url": None,
                    "authorization_user_code": None,
                    "available_models": (),
                    "enabled_models": (),
                    "updated_at": utc_now(),
                }
            )
            saved: Final = await self._repository.save_if_version(blocked, record.version)
            if saved is None:
                return Failure(FailureCode.CONFLICT, "environment was changed by another request")
            try:
                state: Final = record.oauth_provider_state or record.oauth_state
                if state is not None:
                    await self._channel(record).cancel_oauth_session(record, state)
                await self._cli_proxy.delete_auth_file(saved, record.auth_file_name or "")
            except Exception as error:
                await self._log_event(saved, "authentication", error)
                return Failure(FailureCode.UPSTREAM, "认证文件清理失败，卡片已停止接单，请重试删除")
            cleared: Final = saved.model_copy(
                update={
                    "version": saved.version + 1,
                    "auth_file_name": None,
                    "auth_index": None,
                    "auth_file_disabled": False,
                    "quota": QuotaSnapshot(),
                    "model_quotas": (),
                    "cooldown_until": None,
                    "automatic_cooldown": False,
                    "credential_fingerprints": (),
                    "credential_email": None,
                    "credential_account_id": None,
                    "last_error": None,
                    "updated_at": utc_now(),
                }
            )
            completed: Final = await self._repository.save_if_version(cleared, saved.version)
            if completed is None:
                return Failure(FailureCode.CONFLICT, "environment was changed while deleting the credential")
            await self._ownership.retain(record.id)
            return Success(to_view(completed))

    async def patch_auth_file_status(self, environment_id: UUID, disabled: bool) -> Result[EnvironmentView]:
        lock: Final = await self._lock_for(environment_id)
        async with lock, self._ownership.operation(environment_id):
            record: Final = await self._repository.get(environment_id)
            if record is None:
                return Failure(FailureCode.NOT_FOUND, "environment not found")
            if record.channel is not ChannelKind.CLIPROXYAPI or record.auth_file_name is None:
                return Failure(FailureCode.INVALID, "this card has no auth file")
            if record.status is EnvironmentStatus.DELETING:
                return Failure(FailureCode.CONFLICT, "environment is being deleted")
            try:
                await self._cli_proxy.patch_auth_file_status(record, record.auth_file_name, record.auth_index, disabled)
            except Exception as error:
                await self._log_event(record, "authentication", error)
                return Failure(FailureCode.UPSTREAM, "auth file status update failed")
        refreshed: Final = await self._refresh_if_needed(record, credential_state_changed=True, wait_for_lock=True)
        return Success(to_view(refreshed))

    async def patch_auth_file_fields(
        self, environment_id: UUID, fields: Mapping[str, object]
    ) -> Result[EnvironmentView]:
        lock: Final = await self._lock_for(environment_id)
        async with lock, self._ownership.operation(environment_id):
            record: Final = await self._repository.get(environment_id)
            if record is None:
                return Failure(FailureCode.NOT_FOUND, "environment not found")
            if record.channel is not ChannelKind.CLIPROXYAPI or record.auth_file_name is None:
                return Failure(FailureCode.INVALID, "this card has no auth file")
            if not fields or not set(fields).issubset({"priority", "prefix", "proxy_url"}):
                return Failure(FailureCode.INVALID, "auth file fields are required")
            try:
                await self._cli_proxy.patch_auth_file_fields(record, record.auth_file_name, fields)
            except Exception as error:
                await self._log_event(record, "authentication", error)
                return Failure(FailureCode.UPSTREAM, "auth file fields update failed")
        refreshed: Final = await self._refresh_if_needed(record, wait_for_lock=True)
        return Success(to_view(refreshed))

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
