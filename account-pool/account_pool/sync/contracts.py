"""定义渠道生命周期、同步执行器和调用方之间的稳定契约。"""

from __future__ import annotations

from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field, SecretStr, field_validator

from account_pool.auth.actor import ActorContext
from account_pool.catalog.models import AdministrativeState, BindingOwnership
from account_pool.domain.channel_provider_roles import ChannelProviderRoles
from account_pool.models import AccountId, ChannelPriority, FrozenModel, QuotaConfig
from account_pool.sync.litellm import LiteLLMSyncResult, ManagedDeploymentListResult, ManagedDeploymentMarker
from account_pool.sync.models import (
    DeleteMode,
    DesiredBinding,
    ExternalDeploymentDelete,
    SafeSyncFailure,
    SyncOperation,
    SyncStatus,
)


class DeploymentSynchronizer(Protocol):
    async def create_deployment(
        self,
        operation: SyncOperation,
        binding: DesiredBinding,
        api_base: str,
        api_key: SecretStr,
    ) -> LiteLLMSyncResult: ...

    async def update_deployment(
        self,
        operation: SyncOperation,
        binding: DesiredBinding,
        api_base: str,
        api_key: SecretStr | None = None,
    ) -> LiteLLMSyncResult: ...

    async def delete_managed_deployment(self, binding: DesiredBinding) -> LiteLLMSyncResult: ...

    async def delete_external_deployment(self, deletion: ExternalDeploymentDelete) -> LiteLLMSyncResult: ...

    async def list_managed_deployments(self) -> ManagedDeploymentListResult: ...


class ChannelBindingMutation(FrozenModel):
    binding_id: UUID | None = None
    public_model: str = Field(min_length=1)
    provider_model: str | None = None
    litellm_deployment_id: str | None = None
    ownership: BindingOwnership
    enabled: bool = True


class ChannelMutation(FrozenModel):
    legacy_account_id: AccountId | None = None
    display_name: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model_discovery_provider_id: str | None = Field(default=None, min_length=1)
    parser_provider_id: str | None = Field(default=None, min_length=1)
    group: str | None = None
    base_url_display: str = Field(min_length=1)
    administrative_state: AdministrativeState = AdministrativeState.ENABLED
    max_concurrency: int = Field(default=1, ge=1)
    priority: ChannelPriority = ChannelPriority.MEDIUM
    weight: int = Field(default=1, ge=1, le=100)
    quotas: QuotaConfig = QuotaConfig()
    api_key: SecretStr | None = None
    bindings: tuple[ChannelBindingMutation, ...] = Field(min_length=1)

    @field_validator("administrative_state")
    @classmethod
    def reject_internal_pending_delete_state(cls, value: AdministrativeState) -> AdministrativeState:
        if value == AdministrativeState.PENDING_DELETE:
            raise ValueError("pending_delete is managed by the lifecycle service")
        return value

    @property
    def provider_roles(self) -> ChannelProviderRoles:
        return ChannelProviderRoles(
            forwarding_provider=self.provider,
            model_discovery_provider_id=self.model_discovery_provider_id,
            pricing_parser_provider_id=self.parser_provider_id,
        )


class ChannelDeleteRequest(FrozenModel):
    delete_mode: DeleteMode


class ExternalDeploymentDeleteRequest(FrozenModel):
    confirmed: Literal[True]


class ChannelReconcileRequest(FrozenModel):
    api_key: SecretStr | None = None


class ChannelDetail(FrozenModel):
    channel_id: UUID
    display_name: str
    provider: str
    model_discovery_provider_id: str | None = None
    parser_provider_id: str | None = None
    group: str | None
    base_url_display: str
    administrative_state: AdministrativeState
    max_concurrency: int
    priority: ChannelPriority
    weight: int
    quotas: QuotaConfig
    key_mask: str | None
    bindings: tuple[ChannelBindingMutation, ...]

    @property
    def provider_roles(self) -> ChannelProviderRoles:
        return ChannelProviderRoles(
            forwarding_provider=self.provider,
            model_discovery_provider_id=self.model_discovery_provider_id,
            pricing_parser_provider_id=self.parser_provider_id,
        )


class ChannelOperationView(FrozenModel):
    status: Literal["accepted", "existing"]
    operation_id: UUID
    channel_id: UUID
    operation_status: SyncStatus
    requires_key: bool
    failure: SafeSyncFailure | None


class ChannelManagementFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: str
    retryable: bool


ChannelManagementResult = ChannelOperationView | ChannelManagementFailure


class ReconcilePassItem(FrozenModel):
    operation_id: UUID
    channel_id: UUID
    status: Literal["applied", "failed", "requires_key"]
    failure_code: str | None = None


class ReconcilePassResult(FrozenModel):
    inspected: int = Field(ge=0)
    items: tuple[ReconcilePassItem, ...]
    orphan_deployments: tuple[ManagedDeploymentMarker, ...] = ()
    orphan_scan_failure_code: str | None = None


class ChannelManager(Protocol):
    async def detail(self, channel_id: UUID) -> ChannelDetail | ChannelManagementFailure: ...

    async def detail_by_legacy_account(self, account_id: AccountId) -> ChannelDetail | ChannelManagementFailure: ...

    async def operation(self, operation_id: UUID) -> ChannelOperationView | ChannelManagementFailure: ...

    async def create(
        self,
        request: ChannelMutation,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelManagementResult: ...

    async def import_channel(
        self,
        request: ChannelMutation,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelManagementResult: ...

    async def update(
        self,
        channel_id: UUID,
        request: ChannelMutation,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelManagementResult: ...

    async def detach(self, channel_id: UUID, idempotency_key: str, actor: ActorContext) -> ChannelManagementResult: ...

    async def delete(
        self,
        channel_id: UUID,
        request: ChannelDeleteRequest,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelManagementResult: ...

    async def delete_external(
        self,
        channel_id: UUID,
        binding_id: UUID,
        request: ExternalDeploymentDeleteRequest,
        idempotency_key: str,
        actor: ActorContext,
    ) -> ChannelManagementResult: ...

    async def reconcile(
        self,
        channel_id: UUID,
        request: ChannelReconcileRequest,
        actor: ActorContext,
    ) -> ChannelManagementResult: ...

    async def reconcile_pending(self, limit: int = 100) -> ReconcilePassResult | ChannelManagementFailure: ...
