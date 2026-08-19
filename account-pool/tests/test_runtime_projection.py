"""验证 PostgreSQL 渠道目录刷新 Redis 调度配置时的校验与原子切换边界。"""

from typing import Final

import pytest
from account_pool.models import AccountConfig, AccountSnapshot, DeploymentConfig, PoolConfig
from account_pool.runtime_projection import RuntimeProjector
from pydantic import ValidationError


class FakeAppliedCatalog:
    def __init__(self, config: PoolConfig) -> None:
        self._config: Final = config

    async def projected_config(self) -> PoolConfig:
        return self._config


class InvalidAppliedCatalog:
    async def projected_config(self) -> PoolConfig:
        return PoolConfig(
            accounts=(
                _account(account_id="first", deployment_id="duplicate-deployment"),
                _account(account_id="second", deployment_id="duplicate-deployment"),
            )
        )


class FakeScheduler:
    def __init__(self, config: PoolConfig) -> None:
        self.current_config: PoolConfig = config
        self.reconfigure_calls: tuple[PoolConfig, ...] = ()

    async def reconfigure(self, config: PoolConfig) -> None:
        self.current_config = config
        self.reconfigure_calls = (*self.reconfigure_calls, config)

    async def account_snapshots(self) -> tuple[AccountSnapshot, ...]:
        return ()


class FakeEnricher:
    def __init__(self, config: PoolConfig) -> None:
        self._config: Final = config
        self.calls: tuple[PoolConfig, ...] = ()

    async def enrich(self, config: PoolConfig) -> PoolConfig:
        self.calls = (*self.calls, config)
        return self._config


class FailingEnricher:
    async def enrich(self, config: PoolConfig) -> PoolConfig:
        raise RuntimeError(config.accounts[0].id)


def _account(account_id: str, deployment_id: str) -> AccountConfig:
    return AccountConfig(
        id=account_id,
        display_name=account_id,
        provider="openai",
        base_url_display="https://api.example.test",
        max_concurrency=2,
        deployments=(
            DeploymentConfig(
                public_model="public-model",
                litellm_model_id=deployment_id,
            ),
        ),
    )


def _config(account_id: str, deployment_id: str) -> PoolConfig:
    return PoolConfig(accounts=(_account(account_id=account_id, deployment_id=deployment_id),))


async def test_project_loads_catalog_and_reconfigures_scheduler_with_validated_config() -> None:
    current: Final = _config(account_id="current", deployment_id="current-deployment")
    expected: Final = _config(account_id="catalog", deployment_id="catalog-deployment")
    scheduler: Final = FakeScheduler(current)
    projector: Final = RuntimeProjector(
        catalog=FakeAppliedCatalog(expected),
        scheduler=scheduler,
    )

    projected: Final = await projector.project()

    assert projected == expected
    assert scheduler.current_config == expected
    assert scheduler.reconfigure_calls == (expected,)


async def test_project_leaves_runtime_untouched_when_catalog_projection_is_invalid() -> None:
    current: Final = _config(account_id="current", deployment_id="current-deployment")
    scheduler: Final = FakeScheduler(current)
    projector: Final = RuntimeProjector(
        catalog=InvalidAppliedCatalog(),
        scheduler=scheduler,
    )

    with pytest.raises(ValidationError, match="LiteLLM deployment ids must be unique"):
        await projector.project()

    assert scheduler.current_config == current
    assert scheduler.reconfigure_calls == ()


async def test_project_enriches_catalog_config_before_runtime_reconfigure() -> None:
    current: Final = _config(account_id="current", deployment_id="current-deployment")
    catalog: Final = _config(account_id="catalog", deployment_id="catalog-deployment")
    enriched: Final = _config(account_id="enriched", deployment_id="enriched-deployment")
    scheduler: Final = FakeScheduler(current)
    enricher: Final = FakeEnricher(enriched)
    projector: Final = RuntimeProjector(FakeAppliedCatalog(catalog), scheduler, enricher=enricher)

    assert await projector.project() == enriched
    assert enricher.calls == (catalog,)
    assert scheduler.reconfigure_calls == (enriched,)


async def test_enrichment_failure_leaves_runtime_untouched() -> None:
    current: Final = _config(account_id="current", deployment_id="current-deployment")
    catalog: Final = _config(account_id="catalog", deployment_id="catalog-deployment")
    scheduler: Final = FakeScheduler(current)
    projector: Final = RuntimeProjector(FakeAppliedCatalog(catalog), scheduler, enricher=FailingEnricher())

    with pytest.raises(RuntimeError, match="catalog"):
        await projector.project()

    assert scheduler.current_config == current
    assert scheduler.reconfigure_calls == ()
