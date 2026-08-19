"""把持久化渠道目录投影为现有调度器可直接消费的号池配置。"""

from typing import Final

from account_pool.catalog.models import (
    AdministrativeState,
    BindingOwnership,
    CatalogSnapshot,
    ChannelRecord,
    DeploymentBindingRecord,
)
from account_pool.models import AccountConfig, DeploymentConfig, ModelPolicy, PoolConfig


def project_pool_config(snapshot: CatalogSnapshot) -> PoolConfig:
    channel_ids: Final = frozenset(channel.channel_id for channel in snapshot.channels)
    orphan: Final = next((binding for binding in snapshot.bindings if binding.channel_id not in channel_ids), None)
    if orphan is not None:
        raise ValueError(f"orphan binding {orphan.binding_id} references channel {orphan.channel_id}")

    channels: Final = tuple(sorted(snapshot.channels, key=lambda channel: channel.account_order))
    accounts: Final = tuple(_account_config(channel=channel, bindings=snapshot.bindings) for channel in channels)
    policies: Final = tuple(
        ModelPolicy(model=policy.model, strategy=policy.strategy)
        for policy in sorted(snapshot.policies, key=lambda policy: policy.policy_order)
    )
    return PoolConfig(accounts=accounts, policies=policies)


def _account_config(
    channel: ChannelRecord,
    bindings: tuple[DeploymentBindingRecord, ...],
) -> AccountConfig:
    account_id: Final = channel.legacy_account_id or str(channel.channel_id)
    ordered_bindings: Final = tuple(
        sorted(
            (binding for binding in bindings if binding.channel_id == channel.channel_id),
            key=lambda binding: binding.deployment_order,
        )
    )
    return AccountConfig(
        id=account_id,
        channel_id=channel.channel_id,
        display_name=channel.display_name,
        provider=channel.provider,
        group=channel.group,
        base_url_display=channel.base_url_display,
        enabled=channel.administrative_state == AdministrativeState.ENABLED,
        max_concurrency=channel.max_concurrency,
        priority=channel.priority,
        weight=channel.weight,
        quotas=channel.quotas,
        deployments=tuple(_deployment_config(binding) for binding in ordered_bindings),
    )


def _deployment_config(binding: DeploymentBindingRecord) -> DeploymentConfig:
    return DeploymentConfig(
        public_model=binding.public_model,
        litellm_model_id=binding.litellm_deployment_id,
        binding_id=binding.binding_id,
        provider_model=binding.provider_model,
        managed_by_pool=binding.ownership == BindingOwnership.POOL_MANAGED,
        enabled=binding.enabled,
    )
