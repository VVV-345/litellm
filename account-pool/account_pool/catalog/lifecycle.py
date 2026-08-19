"""定义外部同步成功后原子写入已应用渠道目录的生命周期契约。"""

from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from account_pool.catalog.models import BindingOwnership, ChannelRecord, DeploymentBindingRecord
from account_pool.models import FrozenModel


class DeleteMode(StrEnum):
    DETACH_ONLY = "detach_only"
    DELETE_MANAGED_DEPLOYMENT = "delete_managed_deployment"


class CatalogApplyFailureCode(StrEnum):
    OPERATION_MISMATCH = "operation_mismatch"
    CHANNEL_NOT_FOUND = "channel_not_found"
    BINDING_NOT_FOUND = "binding_not_found"
    STATE_CONFLICT = "state_conflict"
    DATABASE_UNAVAILABLE = "database_unavailable"


class CatalogPendingDeleteSuccess(FrozenModel):
    status: Literal["pending_delete"] = "pending_delete"
    operation_id: UUID


class ExternalSyncSuccess(FrozenModel):
    status: Literal["succeeded"] = "succeeded"
    operation_id: UUID


class ExternalSyncFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    operation_id: UUID
    code: str = Field(min_length=1)
    retryable: bool


ExternalSyncResult = Annotated[ExternalSyncSuccess | ExternalSyncFailure, Field(discriminator="status")]


class _ChannelBindingsCommand(FrozenModel):
    operation_id: UUID
    channel: ChannelRecord
    bindings: tuple[DeploymentBindingRecord, ...]

    @model_validator(mode="after")
    def validate_binding_channels(self) -> Self:
        _validate_bindings(self.channel.channel_id, self.bindings)
        return self


class ApplyChannelCreate(_ChannelBindingsCommand):
    action: Literal["create"] = "create"


class ApplyChannelUpdate(_ChannelBindingsCommand):
    action: Literal["update"] = "update"


class ApplyChannelImport(_ChannelBindingsCommand):
    action: Literal["import"] = "import"


class _ChannelRemovalCommand(FrozenModel):
    operation_id: UUID
    channel_id: UUID
    bindings: tuple[DeploymentBindingRecord, ...]

    @model_validator(mode="after")
    def validate_binding_channels(self) -> Self:
        _validate_bindings(self.channel_id, self.bindings)
        return self


class ApplyChannelDetach(_ChannelRemovalCommand):
    action: Literal["detach"] = "detach"


class ApplyChannelDelete(_ChannelRemovalCommand):
    action: Literal["delete"] = "delete"
    mode: DeleteMode


class ApplyExternalBindingDelete(FrozenModel):
    action: Literal["delete_external_binding"] = "delete_external_binding"
    operation_id: UUID
    channel_id: UUID
    binding: DeploymentBindingRecord

    @model_validator(mode="after")
    def validate_external_binding(self) -> Self:
        if self.binding.channel_id != self.channel_id:
            raise ValueError("binding references a different channel")
        if self.binding.ownership != BindingOwnership.EXTERNALLY_MANAGED:
            raise ValueError("binding must be externally managed")
        return self


CatalogLifecycleCommand = Annotated[
    ApplyChannelCreate
    | ApplyChannelUpdate
    | ApplyChannelImport
    | ApplyChannelDetach
    | ApplyChannelDelete
    | ApplyExternalBindingDelete,
    Field(discriminator="action"),
]


class CatalogApplySuccess(FrozenModel):
    status: Literal["applied"] = "applied"
    operation_id: UUID


class CatalogApplyFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    operation_id: UUID
    code: CatalogApplyFailureCode
    retryable: bool


CatalogPendingDeleteResult = CatalogPendingDeleteSuccess | CatalogApplyFailure
CatalogApplyResult = Annotated[CatalogApplySuccess | CatalogApplyFailure, Field(discriminator="status")]
CatalogLifecycleResult = CatalogApplyResult | ExternalSyncFailure


def _validate_bindings(channel_id: UUID, bindings: tuple[DeploymentBindingRecord, ...]) -> None:
    if any(binding.channel_id != channel_id for binding in bindings):
        raise ValueError("binding references a different channel")
    binding_ids = tuple(binding.binding_id for binding in bindings)
    deployment_ids = tuple(binding.litellm_deployment_id for binding in bindings)
    if len(binding_ids) != len(frozenset(binding_ids)):
        raise ValueError("binding IDs must be unique")
    if len(deployment_ids) != len(frozenset(deployment_ids)):
        raise ValueError("LiteLLM deployment IDs must be unique")
