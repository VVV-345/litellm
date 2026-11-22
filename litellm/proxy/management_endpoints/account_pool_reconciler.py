"""本模块把号池路由快照对账为 LiteLLM 受管 Deployment，不处理页面或环境生命周期。"""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Final, Literal, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter

from litellm._logging import verbose_proxy_logger
from litellm.models.model import LiteLLM_ProxyModelTable
from litellm.proxy.management_endpoints.account_pool_native_routing import (
    RoutingQuota,
    RoutingQuotaWindow,
    RoutingSnapshot,
    snapshots,
)
from litellm.proxy.management_endpoints.account_pool_routing import is_glm_standard_api_base, plan_priority
from litellm.repositories.model_repository import ModelRepository

_MANAGED_BY: Final = "account_pool"
_GLM_REASONING_PARAMS: Final = ("reasoning_effort", "thinking")
_CREATED_BY: Final = "account-pool-reconciler"
_DEFAULT_MANAGER_URL: Final = "http://account-pool:8091"
_DEFAULT_INTERVAL_SECONDS: Final = 30.0
_MIN_INTERVAL_SECONDS: Final = 5.0
_MAX_INTERVAL_SECONDS: Final = 60.0


class QuotaWindow(BaseModel):
    name: str | None = None
    window_minutes: int | None = None
    remaining_percent: float
    resets_at: AwareDatetime | None = None


class QuotaSnapshot(BaseModel):
    observed_at: AwareDatetime | None = None
    refresh_status: Literal["complete", "partial", "failed", "unsupported"] | None = None
    extra_usage_enabled: bool | None = None
    plan_type: str | None = None
    auth_file_plan_type: str | None = None
    subscription_active_until: AwareDatetime | None = None
    windows: tuple[QuotaWindow, ...] = ()

    def routing_quota(self) -> RoutingQuota:
        return RoutingQuota(
            remaining_percent=min((window.remaining_percent for window in self.windows), default=None),
            observed_at=self.observed_at,
            refresh_status=self.refresh_status,
            extra_usage_enabled=self.extra_usage_enabled,
            windows=tuple(
                RoutingQuotaWindow(
                    name=w.name, window_minutes=w.window_minutes, remaining_percent=w.remaining_percent, resets_at=w.resets_at
                )
                for w in self.windows
            ),
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
    policy_version: int = Field(default=0, ge=0)
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
    quota_reserve_5h_percent: int | None = Field(default=None, ge=0, le=100)
    quota_reserve_weekly_percent: int | None = Field(default=None, ge=0, le=100)
    quota_snapshot_max_age: int = 300
    web_search_history: bool = False
    supported_protocols: tuple[Literal["chat", "responses", "messages", "images"], ...] | None = None
    passthrough_enabled: bool = False
    quota_switch_project: bool = True
    quota_switch_preview_model: bool = True
    fallback_enabled: bool = True
    sse_heartbeat_interval_seconds: float | None = Field(default=None, ge=0, le=300, allow_inf_nan=False)
    api_base: str
    api_key: str = Field(min_length=1)
    custom_llm_provider: Literal["openai"] = "openai"
    supported_endpoints: tuple[str, ...] = ()


_GATEWAY_ENVIRONMENTS: Final = TypeAdapter(tuple[GatewayEnvironment, ...])


class AllowedOpenAIParams(BaseModel):
    model_config = ConfigDict(frozen=True)
    allowed_openai_params: tuple[str, ...]


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
    supported_endpoints: tuple[str, ...] = ()
    sse_heartbeat_interval_seconds: float | None = None
    glm_standard_api: bool = False
    synced_routing: tuple[tuple[str, int], ...] | None = field(default=None, compare=False)

    @property
    def routing_items(self) -> tuple[tuple[str, int], ...]:
        return tuple(
            (name, value)
            for name, value in (("weight", self.routing_weight), ("order", self.routing_order))
            if value is not None
        )

    @property
    def routing_defaults(self) -> dict[str, object]:
        return dict(self.routing_items)

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
            **(
                AllowedOpenAIParams(allowed_openai_params=_GLM_REASONING_PARAMS).model_dump(mode="json")
                if self.glm_standard_api
                else MappingProxyType({})
            ),
            **(
                {"keepalive_seconds": self.sse_heartbeat_interval_seconds, "allow_client_keepalive_override": False}
                if self.sse_heartbeat_interval_seconds is not None
                else {}
            ),
        }

    @property
    def model_info(self) -> dict[str, object]:
        return {
            "id": self.id,
            "managed_by": _MANAGED_BY,
            "account_pool_environment_id": self.environment_id,
            "account_pool_model": self.model_name,
            "account_pool_glm_standard_api": self.glm_standard_api,
            "account_pool_native_routing": self.native_routing_migrated,
            "supported_endpoints": self.supported_endpoints,
            "account_pool_sse_heartbeat": self.sse_heartbeat_interval_seconds,
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


class AccountPoolHeartbeatMetadata(BaseModel):
    managed_by: str | None = None
    account_pool_sse_heartbeat: float | None = Field(default=None, ge=0, le=300, allow_inf_nan=False)
    account_pool_native_keepalive: dict[str, object] | None = None

    @classmethod
    def deployment_interval(cls, deployment: Mapping[str, object]) -> float | None:
        info: Final = TypeAdapter(dict[str, object]).validate_python(deployment.get("model_info") or {})
        return cls.model_validate(info).interval_seconds if info.get("managed_by") == _MANAGED_BY else None

    @property
    def interval_seconds(self) -> float | None:
        return self.account_pool_sse_heartbeat if self.managed_by == _MANAGED_BY else None


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
        sync_routing: Final = _routing_needs_sync(current_deployment, deployment)
        if current is None:
            try:
                synced_meta: Final = (
                    {"account_pool_synced_routing": deployment.routing_defaults}
                    if deployment.routing_defaults
                    else {}
                )
                await self._repository.create_model(
                    model_name=deployment.model_name,
                    litellm_params={**deployment.litellm_params, **deployment.routing_defaults},
                    model_info={**deployment.model_info, **synced_meta},
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
        heartbeat: Final = AccountPoolHeartbeatMetadata.model_validate(preserved.model_info or {})
        heartbeat_keys: Final = ("keepalive_seconds", "allow_client_keepalive_override")
        native_keepalive: Final = (
            heartbeat.account_pool_native_keepalive or {}
            if heartbeat.account_pool_sse_heartbeat is not None
            else {key: value for key, value in preserved.litellm_params.items() if key in heartbeat_keys}
        )
        previous_glm: Final = (preserved.model_info or MappingProxyType({})).get(
            "account_pool_glm_standard_api"
        ) is True
        native_allowed: Final = (
            TypeAdapter(tuple[str, ...]).validate_python(
                (preserved.model_info or MappingProxyType({})).get("account_pool_glm_native_allowed_params") or ()
                if previous_glm
                else preserved.litellm_params.get("allowed_openai_params") or ()
            )
            if previous_glm or deployment.glm_standard_api
            else ()
        )
        allowed_override: Final = (
            AllowedOpenAIParams(
                allowed_openai_params=tuple(dict.fromkeys((*native_allowed, *_GLM_REASONING_PARAMS)))
                if deployment.glm_standard_api
                else native_allowed
            ).model_dump(mode="json")
            if previous_glm or deployment.glm_standard_api
            else MappingProxyType({})
        )
        inherited_params: Final = {
            **{key: value for key, value in preserved.litellm_params.items() if key not in heartbeat_keys},
            **native_keepalive,
        }
        routing_override: Final = deployment.routing_defaults if sync_routing else MappingProxyType({})
        preserved_info: Final = preserved.model_info or {}
        synced_routing_info: Final = (
            {"account_pool_synced_routing": deployment.routing_defaults}
            if sync_routing
            else (
                {"account_pool_synced_routing": preserved_info["account_pool_synced_routing"]}
                if "account_pool_synced_routing" in preserved_info
                else {}
            )
        )
        await self._repository.update_model(
            model_id=deployment.id,
            model_name=deployment.model_name,
            litellm_params={
                **inherited_params,
                **deployment.litellm_params,
                **routing_override,
                **allowed_override,
            },
            model_info={
                **preserved_info,
                **deployment.model_info,
                **(
                    MappingProxyType(
                        {
                            "account_pool_glm_native_allowed_params": native_allowed
                            if deployment.glm_standard_api
                            else None
                        }
                    )
                    if previous_glm or deployment.glm_standard_api
                    else MappingProxyType({})
                ),
                "account_pool_native_keepalive": (
                    native_keepalive if deployment.sse_heartbeat_interval_seconds is not None else None
                ),
                "account_pool_native_routing": deployment.native_routing_migrated
                or (current_deployment is not None and current_deployment.native_routing_migrated),
                **synced_routing_info,
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
                policy_version=environment.policy_version,
                quota=environment.quota.routing_quota(),
                model_quotas={
                    item.model: item.quota.routing_quota() for item in environment.model_quotas if item.quota.windows
                },
                model_cooldowns={item.model: item.retry_at for item in environment.model_cooldowns},
                model_aliases=environment.model_aliases,
                quota_reserve_percent=environment.quota_reserve_percent,
                quota_reserve_5h_percent=environment.quota_reserve_5h_percent,
                quota_reserve_weekly_percent=environment.quota_reserve_weekly_percent,
                supported_protocols=environment.supported_protocols,
                web_search_history=environment.web_search_history,
                passthrough_enabled=environment.passthrough_enabled,
                quota_snapshot_max_age=environment.quota_snapshot_max_age,
                quota_switch_project=environment.quota_switch_project,
                quota_switch_preview_model=environment.quota_switch_preview_model,
                fallback_enabled=environment.fallback_enabled,
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


class ReconcilerStatus(BaseModel):
    model_config = ConfigDict(frozen=True)
    last_success_at: AwareDatetime | None = None
    last_failure_at: AwareDatetime | None = None
    last_error: str | None = None
    interval_seconds: float
    age_seconds: float | None
    stale: bool
    snapshots: int


class ReconcilerHealth:
    def __init__(self) -> None:
        self.last_success_at: datetime | None = None
        self.last_failure_at: datetime | None = None
        self.last_error: str | None = None

    def success(self, now: datetime) -> None:
        self.last_success_at = now
        self.last_error = None

    def failure(self, error: Exception, now: datetime) -> None:
        self.last_failure_at = now
        self.last_error = str(error)[:500]

    def status(self, interval_seconds: float, now: datetime) -> ReconcilerStatus:
        age: Final = None if self.last_success_at is None else max(0.0, (now - self.last_success_at).total_seconds())
        return ReconcilerStatus(
            last_success_at=self.last_success_at,
            last_failure_at=self.last_failure_at,
            last_error=self.last_error,
            interval_seconds=interval_seconds,
            age_seconds=age,
            stale=age is None or age > max(60.0, interval_seconds * 3),
            snapshots=len(snapshots.values),
        )


reconciler_health: Final = ReconcilerHealth()


def reconciler_status(now: datetime | None = None) -> ReconcilerStatus:
    """路由快照的新鲜度，用于判断对账是否落后于真实额度。"""
    interval: Final = _poll_interval(os.getenv("ACCOUNT_POOL_RECONCILE_INTERVAL_SECONDS"))
    return reconciler_health.status(interval, now or datetime.now(timezone.utc))


async def _reconciliation_loop(interval_seconds: float) -> None:
    while True:
        try:
            await reconcile_configured_account_pool()
            reconciler_health.success(datetime.now(timezone.utc))
        except Exception as error:
            reconciler_health.failure(error, datetime.now(timezone.utc))
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
        supported_endpoints=(
            tuple(dict.fromkeys((*environment.supported_endpoints, "/v1/messages")))
            if environment.passthrough_enabled
            else environment.supported_endpoints
        ),
        blocked=not environment.routable,
        routing_weight=environment.routing_weight,
        routing_order=environment.routing_order,
        native_routing_migrated=environment.routing_weight is not None and environment.routing_order is not None,
        synced_routing=tuple(
            (name, value)
            for name, value in (("weight", environment.routing_weight), ("order", environment.routing_order))
            if value is not None
        )
        or None,
        sse_heartbeat_interval_seconds=environment.sse_heartbeat_interval_seconds,
        glm_standard_api=is_glm_standard_api_base(environment.api_base),
    )


def _routing_needs_sync(current: ManagedDeployment | None, desired: ManagedDeployment) -> bool:
    if not desired.routing_items:
        return False
    if current is None:
        return True
    if not current.native_routing_migrated and desired.native_routing_migrated:
        return True
    if current.synced_routing is None:
        return True
    return current.synced_routing != desired.routing_items


def _matches(current: ManagedDeployment | None, desired: ManagedDeployment) -> bool:
    from dataclasses import replace

    if current is None:
        return False
    if _routing_needs_sync(current, desired):
        return False
    expected: Final = replace(desired, native_routing_migrated=current.native_routing_migrated)
    return current == expected and (current.native_routing_migrated or not desired.native_routing_migrated)


def _parse_synced_routing(raw: object) -> tuple[tuple[str, int], ...] | None:
    if not isinstance(raw, Mapping):
        return None
    items: list[tuple[str, int]] = []
    for key in ("weight", "order"):
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            items.append((key, value))
    return tuple(items)


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
    heartbeat: Final = AccountPoolHeartbeatMetadata.model_validate(
        _StoredProperties.model_validate(row, from_attributes=True).model_info or {}
    )
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
        supported_endpoints=TypeAdapter(tuple[str, ...]).validate_python(info.get("supported_endpoints", ())),
        sse_heartbeat_interval_seconds=heartbeat.account_pool_sse_heartbeat,
        glm_standard_api=info.get("account_pool_glm_standard_api") is True,
        synced_routing=(
            _parse_synced_routing(info.get("account_pool_synced_routing"))
            if "account_pool_synced_routing" in info
            else None
        ),
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
    clamped: Final = max(_MIN_INTERVAL_SECONDS, min(parsed, _MAX_INTERVAL_SECONDS))
    if clamped != parsed:
        # 间隔过长会让路由快照滞后于真实额度，把请求继续发给已耗尽的卡片。
        verbose_proxy_logger.warning(
            "ACCOUNT_POOL_RECONCILE_INTERVAL_SECONDS=%s is out of range; using %s seconds", raw, clamped
        )
    return clamped
