"""定义渠道目录记录、快照和一次性导入结果的数据契约。"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from account_pool.models import AccountId, ChannelPriority, FrozenModel, ModelName, QuotaConfig, Strategy


class AdministrativeState(StrEnum):
    ENABLED = "enabled"
    PAUSED = "paused"
    DISABLED = "disabled"
    PENDING_DELETE = "pending_delete"


class BindingOwnership(StrEnum):
    POOL_MANAGED = "pool_managed"
    EXTERNALLY_MANAGED = "externally_managed"


class ChannelRecord(FrozenModel):
    channel_id: UUID
    legacy_account_id: AccountId | None = None
    account_order: int = Field(ge=0)
    display_name: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model_discovery_provider_id: str | None = Field(default=None, min_length=1)
    parser_provider_id: str | None = Field(default=None, min_length=1)
    group: str | None = None
    base_url_display: str = Field(min_length=1)
    administrative_state: AdministrativeState
    max_concurrency: int = Field(ge=1)
    priority: ChannelPriority
    weight: int = Field(ge=1, le=100)
    quotas: QuotaConfig
    credential_ref: str | None = None
    key_mask: str | None = None
    key_fingerprint: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class DeploymentBindingRecord(FrozenModel):
    binding_id: UUID
    channel_id: UUID
    deployment_order: int = Field(ge=0)
    public_model: ModelName
    provider_model: str | None = None
    litellm_deployment_id: str = Field(min_length=1)
    ownership: BindingOwnership
    enabled: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ModelPolicyRecord(FrozenModel):
    model: ModelName
    policy_order: int = Field(ge=0)
    strategy: Strategy
    created_at: AwareDatetime
    updated_at: AwareDatetime
    version: int = Field(default=1, ge=1)


class ModelCandidateOverrideRecord(FrozenModel):
    model: ModelName
    binding_id: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    manual_order: int | None = Field(default=None, ge=0)
    weight: int | None = Field(default=None, ge=1, le=100)
    paused: bool = False


class CatalogSnapshot(FrozenModel):
    channels: tuple[ChannelRecord, ...] = ()
    bindings: tuple[DeploymentBindingRecord, ...] = ()
    policies: tuple[ModelPolicyRecord, ...] = ()
    candidate_overrides: tuple[ModelCandidateOverrideRecord, ...] = ()


class ChannelSummary(FrozenModel):
    channel_id: UUID
    display_name: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model_discovery_provider_id: str | None = Field(default=None, min_length=1)
    parser_provider_id: str | None = Field(default=None, min_length=1)
    group: str | None = None
    base_url_display: str = Field(min_length=1)
    administrative_state: AdministrativeState
    max_concurrency: int = Field(ge=1)
    priority: ChannelPriority
    weight: int = Field(ge=1, le=100)
    key_mask: str | None = None
    binding_count: int = Field(ge=0)
    enabled_binding_count: int = Field(ge=0)
    models: tuple[ModelName, ...] = ()
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ChannelList(FrozenModel):
    channels: tuple[ChannelSummary, ...] = ()


class CatalogImport(FrozenModel):
    channels: tuple[ChannelRecord, ...]
    bindings: tuple[DeploymentBindingRecord, ...]
    policies: tuple[ModelPolicyRecord, ...]
    candidate_overrides: tuple[ModelCandidateOverrideRecord, ...] = ()


class ImportConflict(FrozenModel):
    entity: Literal["channel", "binding", "policy"]
    identity: str
    reason: str


class ImportResult(FrozenModel):
    status: Literal["created", "unchanged", "conflict"]
    created_channels: int = Field(default=0, ge=0)
    created_bindings: int = Field(default=0, ge=0)
    created_policies: int = Field(default=0, ge=0)
    conflicts: tuple[ImportConflict, ...] = ()
