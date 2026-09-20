"""更新环境配置与冷却状态，保持配置版本和恢复流程完整。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Final, Literal
from uuid import UUID, uuid4

from account_pool.application.environment_state import (
    _AutomaticCooldownState,
    _configuration_requires_reconciliation,
    _cooldown_until_after_update,
    _status_after_update,
)
from account_pool.application.environments.contracts import (
    ChannelOperation,
    DeleteEnvironmentOperation,
    LockForOperation,
    LogEventOperation,
    RefreshIfNeededOperation,
)
from account_pool.config import validate_proxy_profile_url
from account_pool.credential_ownership import CredentialOwnership
from account_pool.domain import (
    ChannelKind,
    EnvironmentConfiguration,
    EnvironmentRecord,
    EnvironmentStatus,
    EnvironmentView,
    ProxyMode,
    SettingsProfileBaselines,
    UpdateEnvironmentRequest,
    configuration_from_record,
    to_view,
    utc_now,
)
from account_pool.ports import (
    EnvironmentRepository,
    ProxyProfileRepository,
)
from account_pool.shared.error_safety import safe_error as _safe_error
from account_pool.shared.result import Failure, FailureCode, Result, Success


@dataclass(frozen=True, slots=True)
class EnvironmentConfigurationOperations:
    _channel: ChannelOperation
    _lock_for: LockForOperation
    _log_event: LogEventOperation
    _ownership: CredentialOwnership
    _proxy_profiles: ProxyProfileRepository
    _refresh_if_needed: RefreshIfNeededOperation
    _repository: EnvironmentRepository
    delete_environment: DeleteEnvironmentOperation

    async def update_environment(
        self,
        environment_id: UUID,
        request: UpdateEnvironmentRequest,
        *,
        settings_profile_baselines: SettingsProfileBaselines | Literal["preserve"] = "preserve",
    ) -> Result[EnvironmentView]:
        lock: Final = await self._lock_for(environment_id)
        async with lock, self._ownership.operation(environment_id):
            record: Final = await self._repository.get(environment_id)
            if record is None:
                return Failure(FailureCode.NOT_FOUND, "environment not found")
            if request.operation_id is not None and record.operation_id == request.operation_id:
                if (
                    record.configuration_pending
                    or record.desired_configuration_version > record.observed_configuration_version
                ):
                    desired: Final = record.desired_configuration or configuration_from_record(record)
                    return await self.apply_and_persist_configuration(record, desired)
                return Success(to_view(record))
            if record.auth_file_name is None and record.status not in (
                EnvironmentStatus.AWAITING_AUTHORIZATION,
                EnvironmentStatus.ERROR,
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
            profile_result: Final = await self.resolve_proxy(request)
            if isinstance(profile_result, Failure):
                return profile_result
            automatic_cooldown: Final = await self.automatic_cooldown_before_update(record, request.manual_cooldown)
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
                    "auth_file_disabled": (
                        not credential_enabled
                        if record.channel is ChannelKind.CLIPROXYAPI and record.auth_file_name is not None
                        else record.auth_file_disabled
                    ),
                    "settings_profile_baselines": (
                        record.settings_profile_baselines
                        if settings_profile_baselines == "preserve"
                        else settings_profile_baselines
                    ),
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
            return await self.apply_and_persist_configuration(claimed, desired_configuration)

    async def reconcile_pending_configurations(self) -> tuple[EnvironmentView, ...]:
        """Manager 启动或后台循环时重复收敛所有未完成配置操作。"""
        records: Final = await self._repository.list()
        results: Final = await asyncio.gather(
            *(
                self.reconcile_configuration(record)
                for record in records
                if _configuration_requires_reconciliation(record)
            )
        )
        return tuple(result.value for result in results if isinstance(result, Success))

    async def reconcile_pending_authorizations(self) -> None:
        """关闭页面后仍由后台继续验证已接收的授权，不重复领取或写入凭据。"""
        records: Final = await self._repository.list()
        await asyncio.gather(
            *(self._refresh_if_needed(record) for record in records if record.status is EnvironmentStatus.VALIDATING)
        )

    async def reconcile_pending_deletions(self) -> None:
        """后台重试已进入删除态但尚未完成资源回收的卡片。"""
        records: Final = await self._repository.list()
        await asyncio.gather(
            *(
                self.delete_environment(record.id, record.operation_id)
                for record in records
                if record.status is EnvironmentStatus.DELETING
            )
        )

    async def reconcile_configuration(self, record: EnvironmentRecord) -> Result[EnvironmentView]:
        lock: Final = await self._lock_for(record.id)
        async with lock, self._ownership.operation(record.id):
            # 锁等待期间记录可能已删除、完成或进入删除态，重新读取后禁止执行陈旧副作用。
            current: Final = await self._repository.get(record.id)
            if current is None:
                return Failure(FailureCode.NOT_FOUND, "environment not found")
            if current.status is EnvironmentStatus.DELETING or not _configuration_requires_reconciliation(current):
                return Success(to_view(current))
            desired: Final = current.desired_configuration or configuration_from_record(current)
            return await self.apply_and_persist_configuration(current, desired)

    async def apply_and_persist_configuration(
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

    async def automatic_cooldown_before_update(
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
                if await self.data_plane_health_check(record)
                else _AutomaticCooldownState.BLOCKED
            )
        if record.manual_cooldown:
            return _AutomaticCooldownState.RECOVERED
        return (
            _AutomaticCooldownState.ACTIVE
            if record.status == EnvironmentStatus.COOLING_DOWN
            else _AutomaticCooldownState.NONE
        )

    async def data_plane_health_check(self, record: EnvironmentRecord) -> bool:
        channel: Final = self._channel(record)
        return await channel.data_plane_health_check(record)

    async def resolve_proxy(self, request: UpdateEnvironmentRequest) -> Result[str]:
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
