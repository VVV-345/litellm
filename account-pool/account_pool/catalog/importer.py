from typing import Final

from pydantic import AwareDatetime

from account_pool.catalog.identity import legacy_binding_id, legacy_channel_id
from account_pool.catalog.models import (
    AdministrativeState,
    BindingOwnership,
    CatalogImport,
    ChannelRecord,
    DeploymentBindingRecord,
    ModelPolicyRecord,
)
from account_pool.models import AccountConfig, DeploymentConfig, FrozenModel, ModelPolicy, PoolConfig


class _ImportTimestamp(FrozenModel):
    value: AwareDatetime


def catalog_import_from_pool_config(config: PoolConfig, imported_at: AwareDatetime) -> CatalogImport:
    validated_at: Final = _ImportTimestamp(value=imported_at).value
    channels: Final = tuple(
        _channel_record(account=account, account_order=account_order, imported_at=validated_at)
        for account_order, account in enumerate(config.accounts)
    )
    bindings: Final = tuple(
        _binding_record(
            account=account,
            deployment=deployment,
            deployment_order=deployment_order,
            imported_at=validated_at,
        )
        for account in config.accounts
        for deployment_order, deployment in enumerate(account.deployments)
    )
    policies: Final = tuple(
        _policy_record(policy=policy, policy_order=policy_order, imported_at=validated_at)
        for policy_order, policy in enumerate(config.policies)
    )
    return CatalogImport(channels=channels, bindings=bindings, policies=policies)


def _channel_record(account: AccountConfig, account_order: int, imported_at: AwareDatetime) -> ChannelRecord:
    return ChannelRecord(
        channel_id=legacy_channel_id(account.id),
        legacy_account_id=account.id,
        account_order=account_order,
        display_name=account.display_name,
        provider=account.provider,
        group=account.group,
        base_url_display=account.base_url_display,
        administrative_state=AdministrativeState.ENABLED if account.enabled else AdministrativeState.DISABLED,
        max_concurrency=account.max_concurrency,
        priority=account.priority,
        weight=account.weight,
        quotas=account.quotas,
        created_at=imported_at,
        updated_at=imported_at,
    )


def _binding_record(
    account: AccountConfig,
    deployment: DeploymentConfig,
    deployment_order: int,
    imported_at: AwareDatetime,
) -> DeploymentBindingRecord:
    channel_id: Final = legacy_channel_id(account.id)
    return DeploymentBindingRecord(
        binding_id=legacy_binding_id(channel_id, deployment.litellm_model_id),
        channel_id=channel_id,
        deployment_order=deployment_order,
        public_model=deployment.public_model,
        provider_model=deployment.provider_model,
        litellm_deployment_id=deployment.litellm_model_id,
        ownership=(
            BindingOwnership.POOL_MANAGED if deployment.managed_by_pool else BindingOwnership.EXTERNALLY_MANAGED
        ),
        enabled=deployment.enabled,
        created_at=imported_at,
        updated_at=imported_at,
    )


def _policy_record(policy: ModelPolicy, policy_order: int, imported_at: AwareDatetime) -> ModelPolicyRecord:
    return ModelPolicyRecord(
        model=policy.model,
        policy_order=policy_order,
        strategy=policy.strategy,
        created_at=imported_at,
        updated_at=imported_at,
    )
