"""从渠道输入或目录快照构造无凭证的同步期望状态。"""

from __future__ import annotations

from hashlib import sha256
from typing import Final, Literal
from uuid import UUID, uuid5

from pydantic import AwareDatetime

from account_pool.catalog.lifecycle import (
    ApplyChannelCreate,
    ApplyChannelDelete,
    ApplyChannelDetach,
    ApplyChannelImport,
    ApplyChannelUpdate,
    ApplyExternalBindingDelete,
    CatalogLifecycleCommand,
)
from account_pool.catalog.lifecycle import (
    DeleteMode as CatalogDeleteMode,
)
from account_pool.catalog.models import BindingOwnership, CatalogSnapshot, ChannelRecord, DeploymentBindingRecord
from account_pool.sync.contracts import ChannelBindingMutation, ChannelMutation
from account_pool.sync.models import (
    ChannelDesiredState,
    DeleteMode,
    DesiredBinding,
    SyncAction,
    SyncOperation,
    SyncStatus,
)

_IDEMPOTENCY_NAMESPACE: Final = UUID("4b5bf3b9-3992-4bd7-b475-3f4b5bdd955d")


def build_desired_state(
    request: ChannelMutation,
    channel_id: UUID,
    account_order: int,
    now: AwareDatetime,
    existing: ChannelRecord | None,
    existing_bindings: tuple[DeploymentBindingRecord, ...],
    operation_id: UUID,
) -> ChannelDesiredState:
    """只保存脱敏后的凭证标识，避免同步操作携带原始 Key。"""

    key_value: Final = request.api_key.get_secret_value() if request.api_key is not None else None
    roles: Final = request.provider_roles
    bindings: Final = tuple(
        DesiredBinding(
            binding_id=binding.binding_id or uuid5(operation_id, f"binding:{index}"),
            channel_id=channel_id,
            deployment_order=index,
            public_model=binding.public_model,
            provider_model=binding.provider_model,
            litellm_deployment_id=binding.litellm_deployment_id or str(uuid5(operation_id, f"deployment:{index}")),
            ownership=binding.ownership,
            sync_mode=_binding_sync_mode(binding, existing_bindings),
            enabled=binding.enabled,
        )
        for index, binding in enumerate(request.bindings)
    )
    parser_provider_id: Final = (
        request.parser_provider_id
        if "parser_provider_id" in request.model_fields_set
        else (None if existing is None else existing.parser_provider_id)
    )
    return ChannelDesiredState(
        channel_id=channel_id,
        legacy_account_id=(
            request.legacy_account_id
            if request.legacy_account_id is not None
            else (None if existing is None else existing.legacy_account_id)
        ),
        account_order=account_order,
        display_name=request.display_name,
        provider=roles.forwarding_provider,
        model_discovery_provider_id=(
            roles.model_discovery_provider_id
            if "model_discovery_provider_id" in request.model_fields_set
            else (None if existing is None else existing.model_discovery_provider_id)
        ),
        parser_provider_id=parser_provider_id,
        group=request.group,
        base_url_display=request.base_url_display,
        administrative_state=request.administrative_state,
        max_concurrency=request.max_concurrency,
        priority=request.priority,
        weight=request.weight,
        quotas=request.quotas,
        credential_ref=None if existing is None else existing.credential_ref,
        key_mask=key_mask(key_value) if key_value is not None else (None if existing is None else existing.key_mask),
        key_fingerprint=(
            sha256(key_value.encode()).hexdigest()
            if key_value is not None
            else (None if existing is None else existing.key_fingerprint)
        ),
        bindings=bindings,
        retired_bindings=tuple(retired_binding(binding) for binding in removed_bindings(request, existing_bindings)),
    )


def desired_state_from_snapshot(snapshot: CatalogSnapshot, channel_id: UUID) -> ChannelDesiredState | None:
    channel: Final = next((item for item in snapshot.channels if item.channel_id == channel_id), None)
    if channel is None:
        return None
    bindings: Final = tuple(
        DesiredBinding(
            binding_id=binding.binding_id,
            channel_id=binding.channel_id,
            deployment_order=binding.deployment_order,
            public_model=binding.public_model,
            provider_model=binding.provider_model,
            litellm_deployment_id=binding.litellm_deployment_id,
            ownership=binding.ownership,
            sync_mode="none",
            enabled=binding.enabled,
        )
        for binding in snapshot.bindings
        if binding.channel_id == channel_id
    )
    return ChannelDesiredState(
        channel_id=channel.channel_id,
        legacy_account_id=channel.legacy_account_id,
        account_order=channel.account_order,
        display_name=channel.display_name,
        provider=channel.provider,
        model_discovery_provider_id=channel.model_discovery_provider_id,
        parser_provider_id=channel.parser_provider_id,
        group=channel.group,
        base_url_display=channel.base_url_display,
        administrative_state=channel.administrative_state,
        max_concurrency=channel.max_concurrency,
        priority=channel.priority,
        weight=channel.weight,
        quotas=channel.quotas,
        credential_ref=channel.credential_ref,
        key_mask=channel.key_mask,
        key_fingerprint=channel.key_fingerprint,
        bindings=bindings,
        retired_bindings=(),
    )


def new_operation(
    operation_id: UUID,
    idempotency_key: str,
    action: SyncAction,
    desired: ChannelDesiredState,
    now: AwareDatetime,
    delete_mode: DeleteMode | None = None,
) -> SyncOperation:
    pending: Final = {
        SyncAction.CREATE_CHANNEL: SyncStatus.PENDING_CREATE,
        SyncAction.IMPORT_CHANNEL: SyncStatus.PENDING_CREATE,
        SyncAction.UPDATE_CHANNEL: SyncStatus.PENDING_UPDATE,
        SyncAction.DETACH_CHANNEL: SyncStatus.PENDING_DELETE,
        SyncAction.DELETE_CHANNEL: SyncStatus.PENDING_DELETE,
        SyncAction.DELETE_EXTERNAL_DEPLOYMENT: SyncStatus.PENDING_DELETE,
    }[action]
    return SyncOperation(
        operation_id=operation_id,
        idempotency_key=idempotency_key,
        channel_id=desired.channel_id,
        action=action,
        status=pending,
        delete_mode=delete_mode,
        desired=desired,
        created_at=now,
        updated_at=now,
    )


def catalog_command(
    operation: SyncOperation,
    external_binding_id: UUID | None,
    now: AwareDatetime,
) -> CatalogLifecycleCommand:
    desired: Final = operation.desired
    channel: Final = ChannelRecord(
        channel_id=desired.channel_id,
        legacy_account_id=desired.legacy_account_id,
        account_order=desired.account_order,
        display_name=desired.display_name,
        provider=desired.provider,
        model_discovery_provider_id=desired.model_discovery_provider_id,
        parser_provider_id=desired.parser_provider_id,
        group=desired.group,
        base_url_display=desired.base_url_display,
        administrative_state=desired.administrative_state,
        max_concurrency=desired.max_concurrency,
        priority=desired.priority,
        weight=desired.weight,
        quotas=desired.quotas,
        credential_ref=desired.credential_ref,
        key_mask=desired.key_mask,
        key_fingerprint=desired.key_fingerprint,
        created_at=operation.created_at,
        updated_at=now,
    )
    bindings: Final = tuple(
        DeploymentBindingRecord(
            binding_id=binding.binding_id,
            channel_id=binding.channel_id,
            deployment_order=binding.deployment_order,
            public_model=binding.public_model,
            provider_model=binding.provider_model,
            litellm_deployment_id=binding.litellm_deployment_id,
            ownership=binding.ownership,
            enabled=binding.enabled,
            created_at=operation.created_at,
            updated_at=now,
        )
        for binding in desired.bindings
    )
    if operation.action == SyncAction.CREATE_CHANNEL:
        return ApplyChannelCreate(operation_id=operation.operation_id, channel=channel, bindings=bindings)
    if operation.action == SyncAction.IMPORT_CHANNEL:
        return ApplyChannelImport(operation_id=operation.operation_id, channel=channel, bindings=bindings)
    if operation.action == SyncAction.UPDATE_CHANNEL:
        return ApplyChannelUpdate(operation_id=operation.operation_id, channel=channel, bindings=bindings)
    if operation.action == SyncAction.DETACH_CHANNEL:
        return ApplyChannelDetach(operation_id=operation.operation_id, channel_id=desired.channel_id, bindings=bindings)
    if operation.action == SyncAction.DELETE_CHANNEL:
        assert operation.delete_mode is not None
        return ApplyChannelDelete(
            operation_id=operation.operation_id,
            channel_id=desired.channel_id,
            bindings=bindings,
            mode=CatalogDeleteMode(operation.delete_mode.value),
        )
    binding: Final = next(item for item in bindings if item.binding_id == external_binding_id)
    return ApplyExternalBindingDelete(
        operation_id=operation.operation_id,
        channel_id=desired.channel_id,
        binding=binding,
    )


def operation_id_from_idempotency_key(idempotency_key: str) -> UUID:
    return uuid5(_IDEMPOTENCY_NAMESPACE, idempotency_key)


def key_mask(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}***{value[-4:]}"


def _binding_sync_mode(
    binding: ChannelBindingMutation,
    existing_bindings: tuple[DeploymentBindingRecord, ...],
) -> Literal["create", "update", "none"]:
    if binding.ownership == BindingOwnership.EXTERNALLY_MANAGED:
        return "none"
    matched: Final = next((item for item in existing_bindings if item.binding_id == binding.binding_id), None)
    return "update" if matched is not None else "create"


def removed_bindings(
    request: ChannelMutation,
    existing_bindings: tuple[DeploymentBindingRecord, ...],
) -> tuple[DeploymentBindingRecord, ...]:
    retained_ids: Final = frozenset(
        binding.binding_id for binding in request.bindings if binding.binding_id is not None
    )
    return tuple(binding for binding in existing_bindings if binding.binding_id not in retained_ids)


def retired_binding(binding: DeploymentBindingRecord) -> DesiredBinding:
    return DesiredBinding(
        binding_id=binding.binding_id,
        channel_id=binding.channel_id,
        deployment_order=binding.deployment_order,
        public_model=binding.public_model,
        provider_model=binding.provider_model,
        litellm_deployment_id=binding.litellm_deployment_id,
        ownership=binding.ownership,
        sync_mode="none",
        enabled=False,
    )
