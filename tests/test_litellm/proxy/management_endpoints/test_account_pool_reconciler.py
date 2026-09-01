"""本文件验证号池环境到 LiteLLM Deployment 的确定性对账。"""

from __future__ import annotations

from dataclasses import replace
from typing import Final
from uuid import uuid4

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
    )


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
