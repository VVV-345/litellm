"""执行渠道到 LiteLLM 的外部 Deployment 同步，不涉及目录或审计。"""

from __future__ import annotations

from typing import Final
from uuid import UUID

from pydantic import SecretStr

from account_pool.catalog.models import BindingOwnership, DeploymentBindingRecord
from account_pool.sync.contracts import DeploymentSynchronizer
from account_pool.sync.litellm import (
    LiteLLMSyncAction,
    LiteLLMSyncFailure,
    LiteLLMSyncResult,
    LiteLLMSyncSuccess,
    ManagedDeploymentListSuccess,
    ManagedDeploymentMarker,
)
from account_pool.sync.models import (
    DeleteMode,
    DesiredBinding,
    ExternalDeploymentDelete,
    SafeSyncFailure,
    SyncAction,
    SyncOperation,
)


async def synchronize_operation(
    synchronizer: DeploymentSynchronizer,
    operation: SyncOperation,
    api_key: SecretStr | None,
    external_binding_id: UUID | None,
) -> SafeSyncFailure | None:
    if operation.action in {SyncAction.IMPORT_CHANNEL, SyncAction.DETACH_CHANNEL}:
        return None
    if operation.action == SyncAction.DELETE_EXTERNAL_DEPLOYMENT:
        if external_binding_id is None:
            return SafeSyncFailure(code="external_binding_not_found", message="External binding is required")
        binding: Final = next(
            (item for item in operation.desired.bindings if item.binding_id == external_binding_id),
            None,
        )
        if binding is None:
            return SafeSyncFailure(code="external_binding_not_found", message="External binding was not found")
        result: Final = await synchronizer.delete_external_deployment(
            ExternalDeploymentDelete(
                channel_id=operation.channel_id,
                binding_id=binding.binding_id,
                litellm_deployment_id=binding.litellm_deployment_id,
                ownership=BindingOwnership.EXTERNALLY_MANAGED,
                confirmed=True,
            )
        )
        return result.failure if isinstance(result, LiteLLMSyncFailure) else None
    if operation.action == SyncAction.DELETE_CHANNEL:
        if operation.delete_mode in {None, DeleteMode.DETACH_ONLY}:
            return None
        return await delete_managed_bindings(synchronizer, operation.desired.bindings)
    if api_key is None and operation.action == SyncAction.CREATE_CHANNEL:
        return SafeSyncFailure(code="provider_key_required", message="Provider key is required")
    active_failure: Final = await synchronize_managed_bindings(synchronizer, operation, api_key)
    if active_failure is not None:
        return active_failure
    return await delete_managed_bindings(synchronizer, operation.desired.retired_bindings)


def retry_requires_key(operation: SyncOperation, api_key: SecretStr | None) -> bool:
    if api_key is not None or operation.action == SyncAction.CREATE_CHANNEL:
        return True
    return any(
        binding.ownership == BindingOwnership.POOL_MANAGED and binding.sync_mode == "create"
        for binding in operation.desired.bindings
    )


def orphan_deployments(
    discovered: ManagedDeploymentListSuccess,
    bindings: tuple[DeploymentBindingRecord, ...],
) -> tuple[ManagedDeploymentMarker, ...]:
    return tuple(
        marker
        for marker in discovered.deployments
        if not any(
            binding.litellm_deployment_id == marker.litellm_deployment_id
            and binding.channel_id == marker.channel_id
            and binding.binding_id == marker.binding_id
            for binding in bindings
        )
    )


async def synchronize_managed_bindings(
    synchronizer: DeploymentSynchronizer,
    operation: SyncOperation,
    api_key: SecretStr | None,
) -> SafeSyncFailure | None:
    for binding in operation.desired.bindings:
        if binding.ownership != BindingOwnership.POOL_MANAGED:
            continue
        match await synchronize_binding(synchronizer, operation, binding, api_key):
            case LiteLLMSyncFailure(failure=failure):
                return failure
            case LiteLLMSyncSuccess():
                continue
    return None


async def delete_managed_bindings(
    synchronizer: DeploymentSynchronizer,
    bindings: tuple[DesiredBinding, ...],
) -> SafeSyncFailure | None:
    for binding in bindings:
        if binding.ownership != BindingOwnership.POOL_MANAGED:
            continue
        match await synchronizer.delete_managed_deployment(binding):
            case LiteLLMSyncFailure(failure=failure):
                return failure
            case LiteLLMSyncSuccess():
                continue
    return None


async def synchronize_binding(
    synchronizer: DeploymentSynchronizer,
    operation: SyncOperation,
    binding: DesiredBinding,
    api_key: SecretStr | None,
) -> LiteLLMSyncResult:
    if operation.action == SyncAction.CREATE_CHANNEL or binding.sync_mode == "create":
        if api_key is None:
            return LiteLLMSyncFailure(
                action=LiteLLMSyncAction.CREATE,
                failure=SafeSyncFailure(code="provider_key_required", message="Provider key is required"),
                retryable=False,
            )
        return await synchronizer.create_deployment(operation, binding, operation.desired.base_url_display, api_key)
    if binding.sync_mode == "none":
        return LiteLLMSyncSuccess(
            action=LiteLLMSyncAction.UPDATE,
            litellm_deployment_id=binding.litellm_deployment_id,
        )
    return await synchronizer.update_deployment(operation, binding, operation.desired.base_url_display, api_key)
