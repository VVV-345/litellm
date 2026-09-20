"""同步全局配置与卡片策略，保留原有条件回滚和并发锁边界。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Final, Literal
from uuid import UUID

from account_pool.application.environments.contracts import (
    ApplyAndPersistConfigurationOperation,
    LockForOperation,
    UpdateEnvironmentOperation,
)
from account_pool.application.profile_updates import (
    _ExplicitProfileUpdate,
    explicit_profile_update,
)
from account_pool.config import validate_proxy_profile_url
from account_pool.credential_ownership import CredentialOwnership
from account_pool.domain import (
    ChannelKind,
    EnvironmentConfiguration,
    EnvironmentRecord,
    EnvironmentStatus,
    ProxyMode,
    configured_proxy_url,
    utc_now,
)
from account_pool.policies import AccountPolicy, PolicyRepository, PolicyView
from account_pool.ports import (
    CLIProxyClient,
    EnvironmentRepository,
    ProxyProfileRepository,
)
from account_pool.settings import AccountPoolSettings, settings_for_card
from account_pool.shared.result import Failure


@dataclass(frozen=True, slots=True)
class EnvironmentSettingsSync:
    _apply_and_persist_configuration: ApplyAndPersistConfigurationOperation
    _cli_proxy: CLIProxyClient
    _lock_for: LockForOperation
    _ownership: CredentialOwnership
    _policies: PolicyRepository | None
    _proxy_profiles: ProxyProfileRepository
    _repository: EnvironmentRepository
    update_environment: UpdateEnvironmentOperation

    async def sync_global_settings(
        self,
        settings: AccountPoolSettings,
        *,
        rollback_on_failure: bool = True,
    ) -> tuple[UUID, ...]:
        records: Final = await self._repository.list()
        policies: Final = () if self._policies is None else await self._policies.list()
        policies_by_card: Final = {policy.card_id: policy for policy in policies}
        updates: Final = tuple(self.explicit_profile_update(record, settings) for record in records)
        results: Final = await asyncio.gather(
            *(
                self.sync_global_settings_for_record(
                    record,
                    settings,
                    update,
                    policies_by_card.get(record.id),
                )
                for record, update in zip(records, updates)
            ),
            return_exceptions=True,
        )
        failed: Final = tuple(record.id for record, result in zip(records, results) if isinstance(result, Exception))
        if not failed or not rollback_on_failure:
            return failed
        rollback_targets: Final = tuple(
            (record, update.request.operation_id)
            for record, update in zip(records, updates)
            if update is not None and update.request.operation_id is not None
        )
        rollback_results: Final = await asyncio.gather(
            *(self.restore_settings_configuration(record, operation_id) for record, operation_id in rollback_targets),
            return_exceptions=True,
        )
        rollback_failed: Final = tuple(
            record.id
            for (record, _), result in zip(rollback_targets, rollback_results)
            if isinstance(result, Exception)
        )
        return tuple(dict.fromkeys((*failed, *rollback_failed)))

    async def sync_global_settings_for_record(
        self,
        record: EnvironmentRecord,
        settings: AccountPoolSettings,
        update: _ExplicitProfileUpdate | None,
        policy: PolicyView | None,
    ) -> None:
        if record.status is EnvironmentStatus.DELETING:
            return
        effective: Final = settings_for_card(settings, record.id)
        configured: Final = await self.apply_explicit_profile_configuration(record, update)
        if configured.channel is not ChannelKind.CLIPROXYAPI:
            return
        await self._cli_proxy.apply_global_settings(configured, effective)
        if policy is None:
            return
        try:
            await self._cli_proxy.apply_policy(configured, policy.policy)
        except Exception:
            await self.set_policy_runtime_status(
                policy,
                "failed",
                "policy runtime synchronization failed",
                require_current=False,
            )
            raise
        await self.set_policy_runtime_status(policy, "synced")

    def explicit_profile_update(
        self,
        record: EnvironmentRecord,
        settings: AccountPoolSettings,
    ) -> _ExplicitProfileUpdate | None:
        return explicit_profile_update(record, settings)

    async def apply_explicit_profile_configuration(
        self,
        record: EnvironmentRecord,
        update: _ExplicitProfileUpdate | None,
    ) -> EnvironmentRecord:
        if update is None:
            return record
        result: Final = await self.update_environment(
            record.id,
            update.request,
            settings_profile_baselines=update.baselines,
        )
        if isinstance(result, Failure):
            raise ValueError(result.message)
        return await self._repository.get(record.id) or record

    async def restore_settings_configuration(self, snapshot: EnvironmentRecord, operation_id: str) -> None:
        lock: Final = await self._lock_for(snapshot.id)
        async with lock, self._ownership.operation(snapshot.id):
            current: Final = await self._repository.get(snapshot.id)
            if current is None:
                raise ValueError("environment not found during settings rollback")
            # 只撤销本次设置同步写入，不能覆盖同步期间发生的其他卡片编辑。
            if current.operation_id != operation_id:
                return
            desired: Final = await self.configuration_for_snapshot(snapshot)
            pending: Final = current.model_copy(
                update={
                    "version": current.version + 1,
                    "name": snapshot.name,
                    "concurrency_limit": snapshot.concurrency_limit,
                    "enabled": snapshot.enabled,
                    "manual_cooldown": snapshot.manual_cooldown,
                    "proxy_mode": snapshot.proxy_mode,
                    "proxy_profile_id": snapshot.proxy_profile_id,
                    "enabled_models": snapshot.enabled_models,
                    "settings_profile_baselines": snapshot.settings_profile_baselines,
                    "status": snapshot.status,
                    "desired_state": snapshot.status,
                    "operation_id": snapshot.operation_id,
                    "desired_configuration_version": current.desired_configuration_version + 1,
                    "desired_configuration": desired,
                    "configuration_pending": True,
                    "configuration_last_error": None,
                    "cooldown_until": snapshot.cooldown_until,
                    "automatic_cooldown": snapshot.automatic_cooldown,
                    "last_error": None,
                    "updated_at": utc_now(),
                }
            )
            claimed: Final = await self._repository.save_if_version(pending, current.version)
            if claimed is None:
                raise ValueError("environment changed during settings rollback")
            applied: Final = await self._apply_and_persist_configuration(claimed, desired)
            if isinstance(applied, Failure):
                raise ValueError(applied.message)
            completed: Final = await self._repository.get(snapshot.id)
            if completed is None:
                raise ValueError("environment disappeared during settings rollback")
            normalized: Final = completed.model_copy(
                update={
                    "version": completed.version + 1,
                    "desired_state": snapshot.desired_state,
                    "operation_id": snapshot.operation_id,
                    "status": snapshot.status,
                    "configuration_last_error": snapshot.configuration_last_error,
                    "last_error": snapshot.last_error,
                    "updated_at": utc_now(),
                }
            )
            if await self._repository.save_if_version(normalized, completed.version) is None:
                raise ValueError("environment changed while finalizing settings rollback")

    async def configuration_for_snapshot(self, snapshot: EnvironmentRecord) -> EnvironmentConfiguration:
        proxy_url: Final = await self.proxy_url_for_snapshot(snapshot)
        previous: Final = snapshot.desired_configuration
        return EnvironmentConfiguration(
            name=snapshot.name,
            concurrency_limit=snapshot.concurrency_limit,
            enabled=snapshot.enabled,
            manual_cooldown=snapshot.manual_cooldown,
            proxy_mode=snapshot.proxy_mode,
            proxy_profile_id=snapshot.proxy_profile_id,
            enabled_models=snapshot.enabled_models,
            proxy_url=proxy_url,
            credential_enabled=(
                previous.credential_enabled
                if previous is not None
                else snapshot.enabled and not snapshot.manual_cooldown and not snapshot.automatic_cooldown
            ),
        )

    async def proxy_url_for_snapshot(self, snapshot: EnvironmentRecord) -> str:
        if snapshot.proxy_mode is ProxyMode.DEFAULT_GATEWAY:
            return ""
        stored_proxy_url: Final = configured_proxy_url(snapshot)
        if stored_proxy_url:
            return validate_proxy_profile_url(stored_proxy_url)
        if snapshot.proxy_profile_id is None:
            raise ValueError("proxy profile is missing during settings rollback")
        profile_url: Final = await self._proxy_profiles.get_url(snapshot.proxy_profile_id)
        if profile_url is None:
            raise ValueError("proxy profile is unavailable during settings rollback")
        return validate_proxy_profile_url(profile_url)

    async def set_policy_runtime_status(
        self,
        policy: PolicyView,
        status: Literal["synced", "failed"],
        error: str | None = None,
        *,
        require_current: bool = True,
    ) -> None:
        if self._policies is None:
            return
        saved: Final = await self._policies.set_runtime_status(policy.card_id, policy.version, status, error)
        if require_current and saved is None:
            raise ValueError("policy changed during runtime synchronization")

    async def sync_policy(self, record: EnvironmentRecord, policy: AccountPolicy) -> None:
        if record.channel is ChannelKind.CLIPROXYAPI and record.status is not EnvironmentStatus.DELETING:
            await self._cli_proxy.apply_policy(record, policy)
