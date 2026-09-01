"""本模块把号池路由快照对账为 LiteLLM 受管 Deployment，不处理页面或环境生命周期。"""

from __future__ import annotations

import asyncio
import math
import os
from dataclasses import dataclass
from typing import Final, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from litellm._logging import verbose_proxy_logger
from litellm.models.model import LiteLLM_ProxyModelTable
from litellm.repositories.model_repository import ModelRepository

_MANAGED_BY: Final = "account_pool"
_CREATED_BY: Final = "account-pool-reconciler"
_DEFAULT_MANAGER_URL: Final = "http://account-pool:8091"
_DEFAULT_INTERVAL_SECONDS: Final = 30.0


class GatewayEnvironment(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    routable: bool
    concurrency_limit: int = Field(ge=1, le=1000)
    enabled_models: tuple[str, ...]
    api_base: str
    api_key: str = Field(min_length=1)


_GATEWAY_ENVIRONMENTS: Final = TypeAdapter(tuple[GatewayEnvironment, ...])


@dataclass(frozen=True, slots=True)
class ManagedDeployment:
    id: str
    environment_id: str
    model_name: str
    provider_model: str
    api_base: str
    api_key: str
    max_parallel_requests: int
    blocked: bool = False

    @property
    def litellm_params(self) -> dict[str, object]:
        return {
            "model": self.provider_model,
            "custom_llm_provider": "openai",
            "api_base": self.api_base,
            "api_key": self.api_key,
            "max_parallel_requests": self.max_parallel_requests,
        }

    @property
    def model_info(self) -> dict[str, object]:
        return {
            "id": self.id,
            "managed_by": _MANAGED_BY,
            "account_pool_environment_id": self.environment_id,
            "account_pool_model": self.model_name,
        }


class DeploymentStore(Protocol):
    async def list_managed(self) -> tuple[ManagedDeployment, ...]: ...

    async def upsert(self, deployment: ManagedDeployment) -> bool: ...

    async def delete(self, deployment_ids: tuple[str, ...]) -> bool: ...

    async def reload(self) -> None: ...


class ManagerGatewayClient:
    def __init__(self, base_url: str, token: str, client: httpx.AsyncClient | None = None) -> None:
        self._base_url: Final = base_url.rstrip("/")
        self._token: Final = token
        self._client: Final = client or httpx.AsyncClient(timeout=30.0, trust_env=False)
        self._owns_client: Final = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def list_environments(self) -> tuple[GatewayEnvironment, ...]:
        response: Final = await self._client.get(
            f"{self._base_url}/internal/gateway/environments",
            headers={"Authorization": f"Bearer {self._token}"},
        )
        response.raise_for_status()
        return _GATEWAY_ENVIRONMENTS.validate_json(response.content)


class LiteLLMDeploymentStore:
    def __init__(self, prisma_client: object) -> None:
        self._repository: Final = ModelRepository(prisma_client)

    async def list_managed(self) -> tuple[ManagedDeployment, ...]:
        rows: Final = await self._repository.find_all()
        return tuple(deployment for row in rows if (deployment := _from_row(row)) is not None)

    async def upsert(self, deployment: ManagedDeployment) -> bool:
        current: Final = await self._repository.find_by_id(deployment.id)
        current_deployment: Final = None if current is None else _from_row(current)
        if current is not None and current_deployment is None:
            raise RuntimeError(f"deployment id {deployment.id} is already owned outside the account pool")
        if current_deployment == deployment and current is not None and not current.blocked:
            return False
        if current is None:
            try:
                await self._repository.create_model(
                    model_name=deployment.model_name,
                    litellm_params=deployment.litellm_params,
                    model_info=deployment.model_info,
                    model_id=deployment.id,
                    created_by=_CREATED_BY,
                )
                return True
            except Exception:
                raced: Final = await self._repository.find_by_id(deployment.id)
                if raced is None or _from_row(raced) is None:
                    raise
        await self._repository.update_model(
            model_id=deployment.id,
            model_name=deployment.model_name,
            litellm_params=deployment.litellm_params,
            model_info=deployment.model_info,
            blocked=False,
            updated_by=_CREATED_BY,
        )
        return True

    async def delete(self, deployment_ids: tuple[str, ...]) -> bool:
        if not deployment_ids:
            return False
        await self._repository.table.delete_many(where={"model_id": {"in": deployment_ids}})
        return True

    async def reload(self) -> None:
        from litellm.proxy.management_endpoints.model_management_endpoints import clear_cache

        await clear_cache()


def desired_deployments(environments: tuple[GatewayEnvironment, ...]) -> tuple[ManagedDeployment, ...]:
    return tuple(
        _deployment(environment, model)
        for environment in environments
        if environment.routable
        for model in environment.enabled_models
    )


async def reconcile(client: ManagerGatewayClient, store: DeploymentStore) -> bool:
    desired: Final = desired_deployments(await client.list_environments())
    current: Final = await store.list_managed()
    desired_by_id: Final = {deployment.id: deployment for deployment in desired}
    current_by_id: Final = {deployment.id: deployment for deployment in current}
    upserted: Final = tuple(
        await asyncio.gather(
            *(
                store.upsert(deployment)
                for deployment_id, deployment in desired_by_id.items()
                if current_by_id.get(deployment_id) != deployment
            )
        )
    )
    stale_ids: Final = tuple(sorted(frozenset(current_by_id).difference(desired_by_id)))
    deleted: Final = await store.delete(stale_ids)
    changed: Final = any(upserted) or deleted
    if changed:
        await store.reload()
    return changed


async def reconcile_configured_account_pool() -> bool:
    token: Final = os.getenv("ACCOUNT_POOL_MANAGER_TOKEN")
    if token is None or len(token) < 32:
        return False
    from litellm.proxy.proxy_server import prisma_client, store_model_in_db

    if prisma_client is None or store_model_in_db is not True:
        raise RuntimeError("Account pool routing requires DATABASE_URL and STORE_MODEL_IN_DB=True")
    client: Final = ManagerGatewayClient(os.getenv("ACCOUNT_POOL_MANAGER_URL", _DEFAULT_MANAGER_URL), token)
    try:
        return await reconcile(client, LiteLLMDeploymentStore(prisma_client))
    finally:
        await client.close()


def start_reconciliation_loop() -> asyncio.Task[None] | None:
    token: Final = os.getenv("ACCOUNT_POOL_MANAGER_TOKEN")
    if token is None or len(token) < 32:
        return None
    interval: Final = _poll_interval(os.getenv("ACCOUNT_POOL_RECONCILE_INTERVAL_SECONDS"))
    return asyncio.create_task(_reconciliation_loop(interval))


async def stop_reconciliation_loop(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        return


async def _reconciliation_loop(interval_seconds: float) -> None:
    while True:
        try:
            await reconcile_configured_account_pool()
        except Exception as error:
            verbose_proxy_logger.warning("Account pool deployment reconciliation failed: %s", error)
        await asyncio.sleep(interval_seconds)


def _deployment(environment: GatewayEnvironment, model: str) -> ManagedDeployment:
    model_name: Final = model.strip()
    deployment_id: Final = str(uuid5(NAMESPACE_URL, f"litellm-account-pool:{environment.id.hex}:{model_name}"))
    return ManagedDeployment(
        id=deployment_id,
        environment_id=str(environment.id),
        model_name=model_name,
        provider_model=f"openai/{model_name}",
        api_base=environment.api_base,
        api_key=environment.api_key,
        max_parallel_requests=environment.concurrency_limit,
    )


def _from_row(row: LiteLLM_ProxyModelTable) -> ManagedDeployment | None:
    info: Final = row.model_info or {}
    params: Final = row.litellm_params
    if info.get("managed_by") != _MANAGED_BY:
        return None
    environment_id: Final = info.get("account_pool_environment_id")
    provider_model: Final = params.get("model")
    api_base: Final = params.get("api_base")
    api_key: Final = params.get("api_key")
    max_parallel_requests: Final = params.get("max_parallel_requests")
    blocked: Final = row.blocked
    if not (
        isinstance(environment_id, str)
        and isinstance(provider_model, str)
        and isinstance(api_base, str)
        and isinstance(api_key, str)
        and isinstance(max_parallel_requests, int)
        and isinstance(blocked, bool)
    ):
        raise RuntimeError(f"managed account pool deployment {row.model_id} is malformed")
    return ManagedDeployment(
        id=row.model_id,
        environment_id=environment_id,
        model_name=row.model_name,
        provider_model=provider_model,
        api_base=api_base,
        api_key=api_key,
        max_parallel_requests=max_parallel_requests,
        blocked=blocked,
    )


def _poll_interval(raw: str | None) -> float:
    if raw is None:
        return _DEFAULT_INTERVAL_SECONDS
    try:
        parsed: Final = float(raw)
    except ValueError:
        return _DEFAULT_INTERVAL_SECONDS
    if not math.isfinite(parsed):
        return _DEFAULT_INTERVAL_SECONDS
    return max(5.0, min(parsed, 3600.0))
