"""验证 Rust 运行时配置契约的稳定版本、字段投影和敏感信息边界。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final

from account_pool.models import (
    AccountConfig,
    DeploymentConfig,
    DeploymentCostEvidence,
    ModelPolicy,
    PoolConfig,
    QuotaWindowConfig,
    RuntimeBillingMode,
    RuntimeQuotaKind,
    RuntimeQuotaScope,
    RuntimeQuotaWindowType,
    Strategy,
)
from account_pool.runtime_contract import build_runtime_config_snapshot

_GENERATED_AT: Final = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def _config(*, weight: int = 3) -> PoolConfig:
    return PoolConfig(
        accounts=(
            AccountConfig(
                id="primary",
                display_name="Primary secret display name",
                provider="openai",
                base_url_display="https://secret-upstream.example/v1",
                max_concurrency=4,
                weight=weight,
                quota_windows=(
                    QuotaWindowConfig(
                        window_id="channel:primary:tokens",
                        scope=RuntimeQuotaScope.CHANNEL,
                        kind=RuntimeQuotaKind.TOKENS,
                        window_type=RuntimeQuotaWindowType.ROLLING,
                        duration_seconds=3600,
                        limit=Decimal("1000"),
                        remaining=Decimal("800.5"),
                        observed_at=1_777_000_000,
                        source="provider",
                        reason_code="provider_quota",
                    ),
                ),
                deployments=(
                    DeploymentConfig(
                        public_model="gpt-test",
                        litellm_model_id="deployment-primary",
                        provider_model="openai/private-provider-model",
                        billing_mode=RuntimeBillingMode.METERED,
                        cost_evidence=DeploymentCostEvidence(
                            kind="effective_prices",
                            currency="USD",
                            unit="million_tokens",
                            input_price=Decimal("1.25"),
                            output_price=Decimal("5.00"),
                            effective_cost=Decimal("6.25"),
                            billing_mode=RuntimeBillingMode.METERED,
                        ),
                    ),
                ),
            ),
        ),
        policies=(ModelPolicy(model="gpt-test", strategy=Strategy.LOWEST_EFFECTIVE_COST, version=2),),
    )


def test_runtime_snapshot_exposes_only_scheduler_fields() -> None:
    snapshot: Final = build_runtime_config_snapshot(
        _config(),
        lease_ttl_seconds=60,
        maximum_lease_seconds=600,
        generated_at=_GENERATED_AT,
    )
    rendered: Final = snapshot.model_dump_json()

    assert snapshot.schema_version == 1
    assert snapshot.accounts[0].deployments[0].litellm_model_id == "deployment-primary"
    assert "secret-upstream" not in rendered
    assert "private-provider-model" not in rendered
    assert "base_url" not in rendered
    assert "api_key" not in rendered
    assert "credential" not in rendered


def test_runtime_snapshot_revision_ignores_generation_time_and_tracks_config() -> None:
    first: Final = build_runtime_config_snapshot(
        _config(),
        lease_ttl_seconds=60,
        maximum_lease_seconds=600,
        generated_at=_GENERATED_AT,
    )
    later: Final = build_runtime_config_snapshot(
        _config(),
        lease_ttl_seconds=60,
        maximum_lease_seconds=600,
        generated_at=_GENERATED_AT + timedelta(minutes=5),
    )
    changed: Final = build_runtime_config_snapshot(
        _config(weight=4),
        lease_ttl_seconds=60,
        maximum_lease_seconds=600,
        generated_at=_GENERATED_AT,
    )

    assert first.revision == later.revision
    assert first.generated_at != later.generated_at
    assert first.revision != changed.revision
