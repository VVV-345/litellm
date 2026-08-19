"""验证旧版 YAML 号池配置到渠道目录记录的转换。"""

from datetime import UTC, datetime
from typing import Final

import pytest
from account_pool.catalog.identity import legacy_binding_id, legacy_channel_id
from account_pool.catalog.importer import catalog_import_from_pool_config
from account_pool.catalog.models import AdministrativeState, BindingOwnership
from account_pool.models import (
    AccountConfig,
    DeploymentConfig,
    ModelPolicy,
    PoolConfig,
    QuotaConfig,
    QuotaUnit,
    Strategy,
)
from pydantic import ValidationError


def legacy_config() -> PoolConfig:
    return PoolConfig(
        accounts=(
            AccountConfig(
                id="z-channel",
                display_name="Zulu",
                provider="openai",
                group="premium",
                base_url_display="https://z.example/v1",
                max_concurrency=3,
                priority=20,
                weight=2,
                quotas=QuotaConfig(unit=QuotaUnit.USD, total=40, five_hour=10, weekly=25),
                deployments=(
                    DeploymentConfig(public_model="z-model", litellm_model_id="deployment-z-external"),
                    DeploymentConfig(
                        public_model="a-model",
                        provider_model="openai/a-model",
                        litellm_model_id="deployment-z-managed",
                        managed_by_pool=True,
                        enabled=False,
                    ),
                ),
            ),
            AccountConfig(
                id="a-channel",
                display_name="Alpha",
                provider="anthropic",
                base_url_display="https://a.example",
                enabled=False,
                max_concurrency=1,
                deployments=(
                    DeploymentConfig(public_model="m-model", litellm_model_id="deployment-a"),
                ),
            ),
        ),
        policies=(
            ModelPolicy(model="z-model", strategy=Strategy.PRIORITY),
            ModelPolicy(model="a-model", strategy=Strategy.LEAST_INFLIGHT),
        ),
    )


def test_import_preserves_order_ownership_and_timestamp() -> None:
    imported_at: Final = datetime(2026, 8, 19, 1, 30, tzinfo=UTC)
    result: Final = catalog_import_from_pool_config(legacy_config(), imported_at)

    assert tuple(channel.legacy_account_id for channel in result.channels) == ("z-channel", "a-channel")
    assert tuple(channel.account_order for channel in result.channels) == (0, 1)
    assert tuple(
        binding.deployment_order
        for binding in result.bindings
        if binding.channel_id == result.channels[0].channel_id
    ) == (0, 1)
    assert tuple(policy.policy_order for policy in result.policies) == (0, 1)
    assert tuple(binding.ownership for binding in result.bindings[:2]) == (
        BindingOwnership.EXTERNALLY_MANAGED,
        BindingOwnership.POOL_MANAGED,
    )
    assert result.channels[1].administrative_state == AdministrativeState.DISABLED
    assert all(record.created_at == imported_at and record.updated_at == imported_at for record in result.channels)
    assert all(record.created_at == imported_at and record.updated_at == imported_at for record in result.bindings)
    assert all(record.created_at == imported_at and record.updated_at == imported_at for record in result.policies)
    assert all(channel.credential_ref is None for channel in result.channels)
    assert "provider-secret" not in result.model_dump_json()


def test_import_uses_deterministic_legacy_identities() -> None:
    imported_at: Final = datetime(2026, 8, 19, 1, 30, tzinfo=UTC)
    result: Final = catalog_import_from_pool_config(legacy_config(), imported_at)
    first_channel: Final = result.channels[0]

    assert first_channel.channel_id == legacy_channel_id("z-channel")
    assert result.bindings[0].binding_id == legacy_binding_id(first_channel.channel_id, "deployment-z-external")


def test_import_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError):
        catalog_import_from_pool_config(legacy_config(), datetime(2026, 8, 19, 1, 30))
