"""本模块把号池路由快照对账为 LiteLLM 受管 Deployment，不处理页面或环境生命周期。"""

from __future__ import annotations

import asyncio
import math
import os
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter

from litellm._logging import verbose_proxy_logger
from litellm.models.model import LiteLLM_ProxyModelTable
from litellm.proxy.management_endpoints.account_pool_native_routing import RoutingQuota, RoutingSnapshot, snapshots
from litellm.proxy.management_endpoints.account_pool_routing import plan_priority
from litellm.repositories.model_repository import ModelRepository

_MANAGED_BY: Final = "account_pool"
_CREATED_BY: Final = "account-pool-reconciler"
_DEFAULT_MANAGER_URL: Final = "http://account-pool:8091"
_DEFAULT_INTERVAL_SECONDS: Final = 30.0


class QuotaWindow(BaseModel):
    remaining_percent: float


class QuotaSnapshot(BaseModel):
    observed_at: AwareDatetime | None = None
    plan_type: str | None = None
    auth_file_plan_type: str | None = None
    subscription_active_until: AwareDatetime | None = None
    windows: tuple[QuotaWindow, ...] = ()

    def routing_quota(self) -> RoutingQuota:
        return RoutingQuota(
            remaining_percent=min((window.remaining_percent for window in self.windows), default=None),
            observed_at=self.observed_at,
        )


class ModelQuota(BaseModel):
    model: str
    quota: QuotaSnapshot


class ModelCooldown(BaseModel):
    model: str
    retry_at: AwareDatetime


class GatewayEnvironment(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    routable: bool
    concurrency_limit: int = Field(ge=0, le=1000)
    enabled_models: tuple[str, ...]
    public_models: tuple[str, ...] | None = None
    routing_weight: int | None = Field(default=None, ge=1, le=10000)
    routing_order: int | None = None
    quota: QuotaSnapshot = Field(default_factory=QuotaSnapshot)
    model_quotas: tuple[ModelQuota, ...] = ()
    model_cooldowns: tuple[ModelCooldown, ...] = ()
    model_aliases: dict[str, str] = Field(default_factory=dict)
    quota_reserve_percent: float = 0
    quota_snapshot_max_age: int = 300
    api_base: str
    api_key: str = Field(min_length=1)
    custom_llm_provider: Literal["openai"] = "openai"


_GATEWAY_ENVIRONMENTS: Final = TypeAdapter(tuple[GatewayEnvironment, ...])


@dataclass(frozen=True, slots=True)
class ManagedDeployment:
    id: str
    environment_id: str
    model_name: str
    provider_model: str
    api_base: str
    api_key: str
    max_parallel_requests: int | None
    custom_llm_provider: Literal["openai"] = "openai"
    blocked: bool = False
    routing_weight: int | None = field(default=None, compare=False)
    routing_order: int | None = field(default=None, compare=False)
    native_routing_migrated: bool = False

    @property
    def routing_defaults(self) -> dict[str, object]:
        return {
            name: value
            for name, value in (("weight", self.routing_weight), ("order", self.routing_order))
            if value is not None
        }

    @property
    def litellm_params(self) -> dict[str, object]:
        return {
            "model": self.provider_model,
            "custom_llm_provider": self.custom_llm_provider,
            "api_base": self.api_base,
            "api_key": self.api_key,
            "max_parallel_requests": self.max_parallel_requests,
            "num_retries": 0,
            "max_retries": 0,
        }

    @property
    def model_info(self) -> dict[str, object]:
        return {
            "id": self.id,
            "managed_by": _MANAGED_BY,
            "account_pool_environment_id": self.environment_id,
            "account_pool_model": self.model_name,
            "account_pool_native_routing": self.native_routing_migrated,
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


class _StoredProperties(BaseModel):
    litellm_params: dict[str, object]
    model_info: dict[str, object] | None = None


class LiteLLMDeploymentStore:
    def __init__(self, prisma_client: object, repository: ModelRepository | None = None) -> None:
        self._repository: Final = repository if repository is not None else ModelRepository(prisma_client)

    async def list_managed(self) -> tuple[ManagedDeployment, ...]:
        rows: Final = await self._repository.find_all()
        return tuple(deployment for row in rows if (deployment := _from_row(row)) is not None)

    async def upsert(self, deployment: ManagedDeployment) -> bool:
        current: Final = await self._repository.find_by_id(deployment.id)
        current_deployment: Final = None if current is None else _from_row(current)
        if current is not None and current_deployment is None:
            raise RuntimeError(f"deployment id {deployment.id} is already owned outside the account pool")
        if _matches(current_deployment, deployment):
            return False
        if current is None:
            try:
                await self._repository.create_model(
                    model_name=deployment.model_name,
                    litellm_params={**deployment.litellm_params, **deployment.routing_defaults},
                    model_info=deployment.model_info,
                    model_id=deployment.id,
                    created_by=_CREATED_BY,
                    blocked=deployment.blocked,
                )
                return True
            except Exception:
                raced: Final = await self._repository.find_by_id(deployment.id)
                if raced is None or _from_row(raced) is None:
                    raise
                return await self.upsert(deployment)
        preserved: Final = _StoredProperties.model_validate(current, from_attributes=True)
        await self._repository.update_model(
            model_id=deployment.id,
            model_name=deployment.model_name,
            litellm_params={
                **deployment.routing_defaults,
                **preserved.litellm_params,
                **deployment.litellm_params,
            },
            model_info={
                **(preserved.model_info or {}),
                **deployment.model_info,
                "account_pool_native_routing": deployment.native_routing_migrated
                or (current_deployment is not None and current_deployment.native_routing_migrated),
            },
            blocked=deployment.blocked,
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
        for model in (
            environment.public_models if environment.public_models is not None else environment.enabled_models
        )
    )


async def reconcile(client: ManagerGatewayClient, store: DeploymentStore) -> bool:
    environments: Final = await client.list_environments()
    snapshots.replace(
        {
            str(environment.id): RoutingSnapshot(
                quota=environment.quota.routing_quota(),
                model_quotas={
                    item.model: item.quota.routing_quota() for item in environment.model_quotas if item.quota.windows
                },
                model_cooldowns={item.model: item.retry_at for item in environment.model_cooldowns},
                model_aliases=environment.model_aliases,
                quota_reserve_percent=environment.quota_reserve_percent,
                quota_snapshot_max_age=environment.quota_snapshot_max_age,
                plan_rank=plan_priority(environment.quota.plan_type, environment.quota.auth_file_plan_type),
                subscription_active_until=environment.quota.subscription_active_until,
            )
            for environment in environments
        }
    )
    desired: Final = desired_deployments(environments)
    current: Final = await store.list_managed()
    desired_by_id: Final = {deployment.id: deployment for deployment in desired}
    current_by_id: Final = {deployment.id: deployment for deployment in current}
    upserted: Final = tuple(
        await asyncio.gather(
            *(
                store.upsert(deployment)
                for deployment_id, deployment in desired_by_id.items()
                if not _matches(current_by_id.get(deployment_id), deployment)
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
        try:
            from litellm.proxy.management_endpoints.account_pool_full_log_api import full_log_maintenance

            await full_log_maintenance().run()
        except Exception:
            verbose_proxy_logger.warning("Account pool full log retention failed")
        await asyncio.sleep(interval_seconds)


def _deployment(environment: GatewayEnvironment, model: str) -> ManagedDeployment:
    from litellm.proxy.management_endpoints.account_pool_integration import forwarding_base

    model_name: Final = model.strip()
    deployment_id: Final = str(uuid5(NAMESPACE_URL, f"litellm-account-pool:{environment.id.hex}:{model_name}"))
    return ManagedDeployment(
        id=deployment_id,
        environment_id=str(environment.id),
        model_name=model_name,
        provider_model=f"openai/{model_name}",
        api_base=forwarding_base(environment.id),
        api_key="account-pool-internal",
        max_parallel_requests=environment.concurrency_limit or None,
        custom_llm_provider=environment.custom_llm_provider,
        blocked=not environment.routable,
        routing_weight=environment.routing_weight,
        routing_order=environment.routing_order,
        native_routing_migrated=environment.routing_weight is not None and environment.routing_order is not None,
    )


def _matches(current: ManagedDeployment | None, desired: ManagedDeployment) -> bool:
    from dataclasses import replace

    if current is None:
        return False
    expected: Final = replace(desired, native_routing_migrated=current.native_routing_migrated)
    return current == expected and (current.native_routing_migrated or not desired.native_routing_migrated)


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
    custom_llm_provider: Final = params.get("custom_llm_provider", "openai")
    blocked: Final = row.blocked
    if not (
        isinstance(environment_id, str)
        and isinstance(provider_model, str)
        and isinstance(api_base, str)
        and isinstance(api_key, str)
        and (max_parallel_requests is None or isinstance(max_parallel_requests, int))
        and custom_llm_provider == "openai"
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
        native_routing_migrated=info.get("account_pool_native_routing") is True,
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
