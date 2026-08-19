"""定义渠道期望状态、同步操作和安全失败信息。"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from account_pool.catalog.models import AdministrativeState, BindingOwnership
from account_pool.models import AccountId, FrozenModel, ModelName, QuotaConfig


class SyncAction(StrEnum):
    CREATE_CHANNEL = "create_channel"
    UPDATE_CHANNEL = "update_channel"
    DETACH_CHANNEL = "detach_channel"
    DELETE_CHANNEL = "delete_channel"
    IMPORT_CHANNEL = "import_channel"
    DELETE_EXTERNAL_DEPLOYMENT = "delete_external_deployment"


class SyncStatus(StrEnum):
    PENDING_CREATE = "pending_create"
    PENDING_UPDATE = "pending_update"
    PENDING_DELETE = "pending_delete"
    APPLIED = "applied"
    FAILED = "failed"


class DeleteMode(StrEnum):
    DETACH_ONLY = "detach_only"
    DELETE_MANAGED_DEPLOYMENT = "delete_managed_deployment"


class DesiredBinding(FrozenModel):
    binding_id: UUID
    channel_id: UUID
    public_model: ModelName
    provider_model: str | None = None
    litellm_deployment_id: str = Field(min_length=1)
    ownership: BindingOwnership
    enabled: bool = True


class ChannelDesiredState(FrozenModel):
    schema_version: Literal[1] = 1
    channel_id: UUID
    legacy_account_id: AccountId | None = None
    display_name: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    group: str | None = None
    base_url_display: str = Field(min_length=1)
    administrative_state: AdministrativeState
    max_concurrency: int = Field(ge=1)
    priority: int
    weight: int = Field(ge=1, le=100)
    quotas: QuotaConfig
    credential_ref: str | None = None
    key_mask: str | None = None
    key_fingerprint: str | None = None
    bindings: tuple[DesiredBinding, ...]

    @model_validator(mode="after")
    def validate_binding_channels_and_ids(self) -> Self:
        if any(binding.channel_id != self.channel_id for binding in self.bindings):
            raise ValueError("desired binding references a different channel")
        binding_ids = tuple(binding.binding_id for binding in self.bindings)
        deployment_ids = tuple(binding.litellm_deployment_id for binding in self.bindings)
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("desired binding IDs must be unique")
        if len(deployment_ids) != len(set(deployment_ids)):
            raise ValueError("desired LiteLLM deployment IDs must be unique")
        return self


class SafeSyncFailure(FrozenModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class SyncOperation(FrozenModel):
    operation_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=255)
    channel_id: UUID
    action: SyncAction
    status: SyncStatus
    delete_mode: DeleteMode | None = None
    desired: ChannelDesiredState
    attempt_count: int = Field(default=0, ge=0)
    requires_key: bool = False
    failure: SafeSyncFailure | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    applied_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_operation_state(self) -> Self:
        expected_pending = {
            SyncAction.CREATE_CHANNEL: SyncStatus.PENDING_CREATE,
            SyncAction.IMPORT_CHANNEL: SyncStatus.PENDING_CREATE,
            SyncAction.UPDATE_CHANNEL: SyncStatus.PENDING_UPDATE,
            SyncAction.DETACH_CHANNEL: SyncStatus.PENDING_DELETE,
            SyncAction.DELETE_CHANNEL: SyncStatus.PENDING_DELETE,
            SyncAction.DELETE_EXTERNAL_DEPLOYMENT: SyncStatus.PENDING_DELETE,
        }[self.action]
        if self.status not in {expected_pending, SyncStatus.APPLIED, SyncStatus.FAILED}:
            raise ValueError("pending status does not match sync action")
        if self.action == SyncAction.DELETE_CHANNEL and self.delete_mode is None:
            raise ValueError("delete_mode is required for channel deletion")
        if self.action != SyncAction.DELETE_CHANNEL and self.delete_mode is not None:
            raise ValueError("delete_mode is only valid for channel deletion")
        if self.status == SyncStatus.APPLIED and self.applied_at is None:
            raise ValueError("applied_at is required for applied operations")
        if self.status != SyncStatus.APPLIED and self.applied_at is not None:
            raise ValueError("applied_at is only valid for applied operations")
        if self.status == SyncStatus.FAILED and self.failure is None:
            raise ValueError("failure is required for failed operations")
        if self.status != SyncStatus.FAILED and self.failure is not None:
            raise ValueError("failure is only valid for failed operations")
        return self


class ExternalDeploymentDelete(FrozenModel):
    channel_id: UUID
    binding_id: UUID
    litellm_deployment_id: str = Field(min_length=1)
    ownership: Literal[BindingOwnership.EXTERNALLY_MANAGED]
    confirmed: Literal[True]
