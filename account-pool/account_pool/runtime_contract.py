"""生成供 Rust 调度网关读取的版本化、无敏感信息运行时配置。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final, Literal
from uuid import UUID

from pydantic import Field, model_validator

from account_pool.models import (
    AccountConfig,
    CostEvidenceKind,
    DeploymentConfig,
    FrozenModel,
    ModelPolicy,
    PoolConfig,
    QuotaConfig,
    QuotaWindowConfig,
    RuntimeBillingMode,
    RuntimeQuotaKind,
    RuntimeQuotaScope,
    RuntimeQuotaWindowType,
    Strategy,
)

RUNTIME_CONFIG_SCHEMA_VERSION: Final = 1


class RuntimeCostEvidence(FrozenModel):
    kind: CostEvidenceKind
    currency: str | None = None
    unit: str | None = None
    input_price: Decimal | None = None
    output_price: Decimal | None = None
    cache_read_price: Decimal | None = None
    cache_write_price: Decimal | None = None
    effective_cost: Decimal
    partial: bool
    provider_group_id: str | None = None
    billing_mode: RuntimeBillingMode


class RuntimeQuotaWindow(FrozenModel):
    window_id: str
    scope: RuntimeQuotaScope
    subject_id: str | None = None
    kind: RuntimeQuotaKind
    window_type: RuntimeQuotaWindowType | None = None
    duration_seconds: int | None = None
    limit: Decimal | None = None
    remaining: Decimal | None = None
    safety_reserve: Decimal
    reset_at: float | None = None
    observed_at: float
    source: str
    reason_code: str


class RuntimeDeployment(FrozenModel):
    public_model: str
    litellm_model_id: str
    binding_id: UUID | None = None
    billing_route_id: str | None = None
    billing_mode: RuntimeBillingMode
    cost_evidence: RuntimeCostEvidence | None = None
    manual_order: int | None = None
    routing_weight: int | None = None
    routing_paused: bool
    enabled: bool


class RuntimeAccount(FrozenModel):
    id: str
    channel_id: UUID | None = None
    enabled: bool
    max_concurrency: int
    priority: int
    weight: int
    quotas: QuotaConfig
    quota_windows: tuple[RuntimeQuotaWindow, ...]
    deployments: tuple[RuntimeDeployment, ...]


class RuntimeModelPolicy(FrozenModel):
    model: str
    strategy: Strategy
    version: int


class RuntimeConfigSnapshot(FrozenModel):
    schema_version: Literal[1] = RUNTIME_CONFIG_SCHEMA_VERSION
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    lease_ttl_seconds: int = Field(ge=1)
    maximum_lease_seconds: int = Field(ge=1)
    accounts: tuple[RuntimeAccount, ...]
    policies: tuple[RuntimeModelPolicy, ...]

    @model_validator(mode="after")
    def validate_lease_duration(self) -> RuntimeConfigSnapshot:
        if self.maximum_lease_seconds < self.lease_ttl_seconds:
            raise ValueError("maximum lease duration must be at least the lease TTL")
        return self


def build_runtime_config_snapshot(
    config: PoolConfig,
    *,
    lease_ttl_seconds: int,
    maximum_lease_seconds: int,
    generated_at: datetime | None = None,
) -> RuntimeConfigSnapshot:
    accounts: Final = tuple(_runtime_account(account) for account in config.accounts)
    policies: Final = tuple(_runtime_policy(policy) for policy in config.policies)
    # 只对外发布调度所需字段，API Key、Base URL 和供应商模型名留在 Python 控制面。
    canonical_payload: Final = {
        "schema_version": RUNTIME_CONFIG_SCHEMA_VERSION,
        "lease_ttl_seconds": lease_ttl_seconds,
        "maximum_lease_seconds": maximum_lease_seconds,
        "accounts": [account.model_dump(mode="json") for account in accounts],
        "policies": [policy.model_dump(mode="json") for policy in policies],
    }
    encoded: Final = json.dumps(
        canonical_payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return RuntimeConfigSnapshot(
        revision=hashlib.sha256(encoded).hexdigest(),
        generated_at=generated_at or datetime.now(UTC),
        lease_ttl_seconds=lease_ttl_seconds,
        maximum_lease_seconds=maximum_lease_seconds,
        accounts=accounts,
        policies=policies,
    )


def _runtime_account(account: AccountConfig) -> RuntimeAccount:
    return RuntimeAccount(
        id=account.id,
        channel_id=account.channel_id,
        enabled=account.enabled,
        max_concurrency=account.max_concurrency,
        priority=account.priority,
        weight=account.weight,
        quotas=account.quotas,
        quota_windows=tuple(_runtime_quota_window(window) for window in account.quota_windows),
        deployments=tuple(_runtime_deployment(deployment) for deployment in account.deployments),
    )


def _runtime_deployment(deployment: DeploymentConfig) -> RuntimeDeployment:
    evidence: Final = deployment.cost_evidence
    return RuntimeDeployment(
        public_model=deployment.public_model,
        litellm_model_id=deployment.litellm_model_id,
        binding_id=deployment.binding_id,
        billing_route_id=deployment.billing_route_id,
        billing_mode=deployment.billing_mode,
        cost_evidence=(
            None
            if evidence is None
            else RuntimeCostEvidence(
                kind=evidence.kind,
                currency=evidence.currency,
                unit=evidence.unit,
                input_price=evidence.input_price,
                output_price=evidence.output_price,
                cache_read_price=evidence.cache_read_price,
                cache_write_price=evidence.cache_write_price,
                effective_cost=evidence.effective_cost,
                partial=evidence.partial,
                provider_group_id=evidence.provider_group_id,
                billing_mode=evidence.billing_mode,
            )
        ),
        manual_order=deployment.manual_order,
        routing_weight=deployment.routing_weight,
        routing_paused=deployment.routing_paused,
        enabled=deployment.enabled,
    )


def _runtime_quota_window(window: QuotaWindowConfig) -> RuntimeQuotaWindow:
    return RuntimeQuotaWindow(
        window_id=window.window_id,
        scope=window.scope,
        subject_id=window.subject_id,
        kind=window.kind,
        window_type=window.window_type,
        duration_seconds=window.duration_seconds,
        limit=window.limit,
        remaining=window.remaining,
        safety_reserve=window.safety_reserve,
        reset_at=window.reset_at,
        observed_at=window.observed_at,
        source=window.source,
        reason_code=window.reason_code,
    )


def _runtime_policy(policy: ModelPolicy) -> RuntimeModelPolicy:
    return RuntimeModelPolicy(model=policy.model, strategy=policy.strategy, version=policy.version)
