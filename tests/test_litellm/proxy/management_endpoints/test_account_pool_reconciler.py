"""本文件验证号池环境到 LiteLLM Deployment 的确定性对账。"""

from __future__ import annotations

from dataclasses import replace
from typing import Final
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from litellm.proxy.management_endpoints.account_pool_reconciler import (
    GatewayEnvironment,
    ManagedDeployment,
    desired_deployments,
    reconcile,
)


class FakeGatewayClient:
    def __init__(self, environments: tuple[GatewayEnvironment, ...]) -> None:
        self.environments: Final = environments

    async def list_environments(self) -> tuple[GatewayEnvironment, ...]:
        return self.environments


class FakeDeploymentStore:
    def __init__(self, deployments: tuple[ManagedDeployment, ...]) -> None:
        self.deployments = {deployment.id: deployment for deployment in deployments}
        self.upserted: list[ManagedDeployment] = []
        self.deleted: tuple[str, ...] = ()
        self.reload_count = 0

    async def list_managed(self) -> tuple[ManagedDeployment, ...]:
        return tuple(self.deployments.values())

    async def upsert(self, deployment: ManagedDeployment) -> bool:
        self.deployments[deployment.id] = deployment
        self.upserted.append(deployment)
        return True

    async def delete(self, deployment_ids: tuple[str, ...]) -> bool:
        self.deleted = deployment_ids
        for deployment_id in deployment_ids:
            self.deployments.pop(deployment_id, None)
        return bool(deployment_ids)

    async def reload(self) -> None:
        self.reload_count += 1


def _environment(*, routable: bool, models: tuple[str, ...] = ("gpt-5",)) -> GatewayEnvironment:
    return GatewayEnvironment(
        id=uuid4(),
        routable=routable,
        concurrency_limit=4,
        enabled_models=models,
        api_base="http://cliproxy.example:8317/v1",
        api_key="gateway-secret",
        custom_llm_provider="openai",
    )


def test_desired_deployments_keep_protocol_identity_stable() -> None:
    environment: Final = _environment(routable=True, models=("gpt-5", "gpt-4.1"))

    deployments: Final = desired_deployments((environment,))

    assert len(deployments) == 2
    for deployment in deployments:
        assert deployment.provider_model == f"openai/{deployment.model_name}"
        assert deployment.litellm_params["custom_llm_provider"] == "openai"
        assert deployment.id == str(
            uuid5(NAMESPACE_URL, f"litellm-account-pool:{environment.id.hex}:{deployment.model_name}")
        )
        assert deployment.model_info["managed_by"] == "account_pool"
        assert deployment.model_info["account_pool_environment_id"] == str(environment.id)
    assert [deployment.model_name for deployment in deployments] == ["gpt-5", "gpt-4.1"]


def test_non_routable_snapshots_keep_disabled_deployments() -> None:
    blocked: Final = _environment(routable=False, models=("gpt-5", "gpt-4.1"))

    deployments: Final = desired_deployments((blocked,))

    assert len(deployments) == 2
    assert all(deployment.blocked for deployment in deployments)


def test_zero_concurrency_limit_removes_litellm_parallel_request_limit() -> None:
    environment: Final = _environment(routable=True).model_copy(update={"concurrency_limit": 0})

    deployment: Final = desired_deployments((environment,))[0]

    assert deployment.max_parallel_requests is None
    assert deployment.litellm_params["max_parallel_requests"] is None


def test_public_aliases_reach_internal_gateway_without_upstream_secrets() -> None:
    environment = _environment(routable=True).model_copy(update={"public_models": ("my-model",)})
    deployment = desired_deployments((environment,))[0]
    assert deployment.model_name == "my-model"
    assert deployment.provider_model == "openai/my-model"
    assert deployment.api_base.endswith(f"/{environment.id}/v1")
    assert deployment.api_key != environment.api_key
    assert deployment.litellm_params["num_retries"] == 0


@pytest.mark.asyncio
async def test_reconcile_only_exposes_routable_models_and_removes_stale_deployments() -> None:
    environment: Final = _environment(routable=True, models=("gpt-5", "gpt-4.1"))
    disabled: Final = _environment(routable=False)
    stale: Final = ManagedDeployment(
        id="stale",
        environment_id=str(disabled.id),
        model_name="gpt-4o",
        provider_model="openai/gpt-4o",
        api_base="http://old/v1",
        api_key="old-secret",
        max_parallel_requests=1,
    )
    store: Final = FakeDeploymentStore((stale,))

    changed: Final = await reconcile(FakeGatewayClient((environment, disabled)), store)

    assert changed is True
    assert {deployment.model_name for deployment in store.upserted} == {"gpt-5", "gpt-4.1"}
    assert store.deleted == ("stale",)
    assert store.reload_count == 1
    assert all(deployment.max_parallel_requests == 4 for deployment in store.upserted)
    assert {
        deployment.model_info["account_pool_environment_id"] for deployment in store.upserted if not deployment.blocked
    } == {str(environment.id)}
    assert {deployment.litellm_params["max_parallel_requests"] for deployment in store.upserted} == {4}


@pytest.mark.asyncio
async def test_reconcile_is_idempotent_for_an_unchanged_snapshot() -> None:
    environment: Final = _environment(routable=True)
    first_store: Final = FakeDeploymentStore(())
    first_changed: Final = await reconcile(FakeGatewayClient((environment,)), first_store)
    second_store: Final = FakeDeploymentStore(tuple(first_store.deployments.values()))

    second_changed: Final = await reconcile(FakeGatewayClient((environment,)), second_store)

    assert first_changed is True
    assert second_changed is False
    assert second_store.upserted == []
    assert second_store.reload_count == 0


@pytest.mark.asyncio
async def test_reconcile_unblocks_a_managed_deployment_when_it_is_still_desired() -> None:
    environment: Final = _environment(routable=True)
    desired: Final = desired_deployments((environment,))[0]
    blocked: Final = replace(desired, blocked=True)
    store: Final = FakeDeploymentStore((blocked,))

    changed: Final = await reconcile(FakeGatewayClient((environment,)), store)

    assert changed is True
    assert store.upserted == [desired]
    assert store.reload_count == 1


@pytest.mark.asyncio
async def test_native_routing_migrates_after_manager_upgrade_and_preserves_native_edits():
    from unittest.mock import AsyncMock

    from litellm.models.model import LiteLLM_ProxyModelTable
    from litellm.proxy.management_endpoints.account_pool_reconciler import LiteLLMDeploymentStore
    from litellm.repositories.model_repository import ModelRepository

    environment = _environment(routable=True)
    old = desired_deployments((environment,))[0]
    row = LiteLLM_ProxyModelTable(
        model_id=old.id,
        model_name=old.model_name,
        litellm_params=old.litellm_params,
        model_info=old.model_info,
        blocked=False,
    )
    repository = AsyncMock(spec=ModelRepository)
    repository.find_by_id.return_value = row
    store = LiteLLMDeploymentStore(None, repository)
    assert await store.upsert(old) is False
    upgraded = desired_deployments((environment.model_copy(update={"routing_weight": 7, "routing_order": -12}),))[0]
    assert await store.upsert(upgraded) is True
    first = repository.update_model.call_args.kwargs
    assert first["litellm_params"]["weight"] == 7
    assert first["litellm_params"]["order"] == -12
    assert first["model_info"]["account_pool_native_routing"] is True
    edited = {**first["litellm_params"], "weight": 20, "order": 3, "timeout": 90, "guardrails": ["guard"]}
    repository.find_by_id.return_value = row.model_copy(
        update={"litellm_params": edited, "model_info": first["model_info"]}
    )
    assert await store.upsert(upgraded) is False
    assert await store.upsert(old) is False
    assert await store.upsert(replace(upgraded, blocked=True)) is True
    saved = repository.update_model.call_args.kwargs
    assert saved["litellm_params"] == edited
    assert saved["blocked"] is True
    assert saved["model_info"]["account_pool_native_routing"] is True
