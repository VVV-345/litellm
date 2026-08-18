"""管理号池渠道，并同步对应的 LiteLLM Deployment。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast
from uuid import uuid4

import httpx
from pydantic import TypeAdapter, ValidationError

from account_pool.config import save_pool_config
from account_pool.models import (
    AccountConfig,
    AccountMutation,
    DeploymentConfig,
    DeploymentInput,
    LiteLLMStatus,
    ManagementResult,
    ModelPolicy,
    PolicyUpdate,
    PoolConfig,
)
from account_pool.scheduler import Scheduler

_JSON_OBJECT: Final = TypeAdapter(dict[str, object])
_OBJECT_LIST: Final = TypeAdapter(list[object])


@dataclass(frozen=True, slots=True)
class AdminDeployment:
    deployment_id: str


@dataclass(frozen=True, slots=True)
class AdminFailure:
    message: str


AdminDeploymentResult = AdminDeployment | AdminFailure


class LiteLLMAdminClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str, admin_key: str | None) -> None:
        self._client = client
        self._base_url = base_url
        self._admin_key = admin_key

    async def status(self) -> LiteLLMStatus:
        try:
            health: Final = await self._client.get(f"{self._base_url}/health/liveliness")
        except httpx.HTTPError:
            return LiteLLMStatus(
                connected=False,
                authenticated=False,
                manageable=False,
                message="LiteLLM 连接失败",
            )
        if health.status_code >= 400:
            return LiteLLMStatus(
                connected=False,
                authenticated=False,
                manageable=False,
                message=f"LiteLLM 健康检查失败 ({health.status_code})",
            )
        if self._admin_key is None:
            return LiteLLMStatus(
                connected=True,
                authenticated=False,
                manageable=False,
                message="LiteLLM 已连接，未配置管理密钥",
            )
        try:
            response: Final = await self._client.get(f"{self._base_url}/model/info", headers=self._headers())
        except httpx.HTTPError:
            return LiteLLMStatus(
                connected=True,
                authenticated=False,
                manageable=False,
                message="LiteLLM 管理接口连接失败",
            )
        if response.status_code >= 400:
            return LiteLLMStatus(
                connected=True,
                authenticated=False,
                manageable=False,
                message=f"LiteLLM 管理认证失败 ({response.status_code})",
            )
        deployment_count: Final = _deployment_count(response.content)
        return LiteLLMStatus(
            connected=True,
            authenticated=True,
            manageable=True,
            deployment_count=deployment_count,
            message="LiteLLM 已连接",
        )

    async def authorize(self, access_token: str) -> bool:
        try:
            response: Final = await self._client.get(
                f"{self._base_url}/account_pool/authorize",
                headers={"authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError:
            return False
        return response.status_code < 400

    async def create_deployment(
        self,
        account_id: str,
        deployment: DeploymentInput,
        api_base: str,
        api_key: str | None,
    ) -> AdminDeploymentResult:
        if self._admin_key is None:
            return AdminFailure(message="未配置 LiteLLM 管理密钥")
        provider_model: Final = deployment.provider_model
        if provider_model is None:
            return AdminFailure(message=f"模型 {deployment.public_model} 缺少供应商模型名")
        deployment_id: Final = uuid4().hex
        litellm_params: Final[dict[str, object]] = {
            "model": provider_model,
            "api_base": api_base,
            **({"api_key": api_key} if api_key else {}),
        }
        result: Final = await self._request(
            method="POST",
            path="/model/new",
            body={
                "model_name": deployment.public_model,
                "litellm_params": litellm_params,
                "model_info": {"id": deployment_id, "account_pool_account_id": account_id},
            },
        )
        if isinstance(result, AdminFailure):
            return result
        returned_id: Final = _deployment_id(result)
        if returned_id is None:
            return AdminFailure(message="LiteLLM 创建成功但未返回 deployment ID")
        return AdminDeployment(deployment_id=returned_id)

    async def update_deployment(
        self,
        deployment_id: str,
        public_model: str,
        provider_model: str | None,
        api_base: str,
        api_key: str | None,
    ) -> AdminFailure | None:
        litellm_params: Final[dict[str, object]] = {
            **({"model": provider_model} if provider_model else {}),
            "api_base": api_base,
            **({"api_key": api_key} if api_key else {}),
        }
        result: Final = await self._request(
            method="PATCH",
            path=f"/model/{deployment_id}/update",
            body={"model_name": public_model, "litellm_params": litellm_params},
        )
        return result if isinstance(result, AdminFailure) else None

    async def delete_deployment(self, deployment_id: str) -> AdminFailure | None:
        result: Final = await self._request(
            method="POST",
            path="/model/delete",
            body={"id": deployment_id},
        )
        return result if isinstance(result, AdminFailure) else None

    async def _request(self, method: str, path: str, body: Mapping[str, object]) -> dict[str, object] | AdminFailure:
        if self._admin_key is None:
            return AdminFailure(message="未配置 LiteLLM 管理密钥")
        try:
            response: Final = await self._client.request(
                method=method,
                url=f"{self._base_url}{path}",
                headers=self._headers(),
                json=dict(body),
            )
        except httpx.HTTPError:
            return AdminFailure(message="LiteLLM 管理接口连接失败")
        if response.status_code >= 400:
            return AdminFailure(message=f"LiteLLM 管理操作失败 ({response.status_code})")
        try:
            return _JSON_OBJECT.validate_json(response.content)
        except ValidationError:
            return AdminFailure(message="LiteLLM 返回了无法识别的数据")

    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self._admin_key}"} if self._admin_key is not None else {}


class PoolManager:
    def __init__(self, scheduler: Scheduler, admin: LiteLLMAdminClient, config_path: Path) -> None:
        self._scheduler = scheduler
        self._admin = admin
        self._config_path = config_path
        self._lock = asyncio.Lock()

    async def create_account(self, request: AccountMutation) -> ManagementResult:
        async with self._lock:
            config: Final = self._scheduler.config()
            if any(account.id == request.id for account in config.accounts):
                return ManagementResult(ok=False, message="渠道 ID 已存在")
            materialized: Final = await self._materialize(request=request, current=None)
            if isinstance(materialized, AdminFailure):
                return ManagementResult(ok=False, message=materialized.message)
            account: Final = _account_config(request=request, deployments=materialized.deployments)
            next_config: Final = PoolConfig(accounts=(*config.accounts, account), policies=config.policies)
            committed: Final = await self._commit(next_config)
            if committed is not None:
                await self._rollback_created(materialized.created_ids)
                return ManagementResult(ok=False, message=committed)
            return ManagementResult(ok=True, message="渠道已创建")

    async def update_account(self, account_id: str, request: AccountMutation) -> ManagementResult:
        async with self._lock:
            config: Final = self._scheduler.config()
            current: Final = next((account for account in config.accounts if account.id == account_id), None)
            if current is None:
                return ManagementResult(ok=False, message="渠道不存在")
            if request.id != account_id and any(account.id == request.id for account in config.accounts):
                return ManagementResult(ok=False, message="渠道 ID 已存在")
            materialized: Final = await self._materialize(request=request, current=current)
            if isinstance(materialized, AdminFailure):
                return ManagementResult(ok=False, message=materialized.message)
            account: Final = _account_config(request=request, deployments=materialized.deployments)
            next_accounts: Final = tuple(account if item.id == account_id else item for item in config.accounts)
            next_config: Final = PoolConfig(accounts=next_accounts, policies=config.policies)
            committed: Final = await self._commit(next_config)
            if committed is not None:
                await self._rollback_created(materialized.created_ids)
                return ManagementResult(ok=False, message=committed)
            retained: Final = {deployment.litellm_model_id for deployment in materialized.deployments}
            removed: Final = tuple(
                deployment.litellm_model_id
                for deployment in current.deployments
                if deployment.managed_by_pool and deployment.litellm_model_id not in retained
            )
            failures: Final = await self._delete_deployments(removed)
            message: Final = "渠道已更新" if failures == 0 else f"渠道已更新，{failures} 个旧 deployment 清理失败"
            return ManagementResult(ok=failures == 0, message=message)

    async def delete_account(self, account_id: str) -> ManagementResult:
        async with self._lock:
            config: Final = self._scheduler.config()
            current: Final = next((account for account in config.accounts if account.id == account_id), None)
            if current is None:
                return ManagementResult(ok=False, message="渠道不存在")
            snapshots: Final = await self._scheduler.account_snapshots()
            snapshot: Final = next((item for item in snapshots if item.account_id == account_id), None)
            if snapshot is not None and snapshot.inflight > 0:
                return ManagementResult(ok=False, message="渠道仍有请求正在处理")
            next_config: Final = PoolConfig(
                accounts=tuple(account for account in config.accounts if account.id != account_id),
                policies=config.policies,
            )
            committed: Final = await self._commit(next_config)
            if committed is not None:
                return ManagementResult(ok=False, message=committed)
            managed_ids: Final = tuple(
                deployment.litellm_model_id for deployment in current.deployments if deployment.managed_by_pool
            )
            failures: Final = await self._delete_deployments(managed_ids)
            message: Final = "渠道已删除" if failures == 0 else f"渠道已删除，{failures} 个 deployment 清理失败"
            return ManagementResult(ok=failures == 0, message=message)

    async def update_policy(self, model: str, request: PolicyUpdate) -> ManagementResult:
        async with self._lock:
            config: Final = self._scheduler.config()
            if model not in self._scheduler.models():
                return ManagementResult(ok=False, message="模型不存在")
            replacement: Final = ModelPolicy(model=model, strategy=request.strategy)
            policies: Final = tuple(policy for policy in config.policies if policy.model != model) + (replacement,)
            committed: Final = await self._commit(PoolConfig(accounts=config.accounts, policies=policies))
            return ManagementResult(ok=committed is None, message=committed or "路由策略已更新")

    async def _materialize(
        self,
        request: AccountMutation,
        current: AccountConfig | None,
    ) -> MaterializedDeployments | AdminFailure:
        existing: Final = {item.litellm_model_id: item for item in current.deployments} if current is not None else {}
        deployments: list[DeploymentConfig] = []
        created_ids: list[str] = []
        api_key: Final = request.api_key.get_secret_value() if request.api_key is not None else None
        for item in request.deployments:
            if item.litellm_model_id is None:
                created = await self._admin.create_deployment(
                    account_id=request.id,
                    deployment=item,
                    api_base=request.base_url_display,
                    api_key=api_key,
                )
                if isinstance(created, AdminFailure):
                    await self._rollback_created(tuple(created_ids))
                    return created
                created_ids.append(created.deployment_id)
                deployments.append(
                    DeploymentConfig(
                        public_model=item.public_model,
                        provider_model=item.provider_model,
                        litellm_model_id=created.deployment_id,
                        managed_by_pool=True,
                        enabled=item.enabled,
                    )
                )
                continue
            previous = existing.get(item.litellm_model_id)
            managed = previous.managed_by_pool if previous is not None else False
            provider_model = item.provider_model or (previous.provider_model if previous is not None else None)
            if managed:
                failure = await self._admin.update_deployment(
                    deployment_id=item.litellm_model_id,
                    public_model=item.public_model,
                    provider_model=provider_model,
                    api_base=request.base_url_display,
                    api_key=api_key,
                )
                if failure is not None:
                    await self._rollback_created(tuple(created_ids))
                    return failure
            deployments.append(
                DeploymentConfig(
                    public_model=item.public_model,
                    provider_model=provider_model,
                    litellm_model_id=item.litellm_model_id,
                    managed_by_pool=managed,
                    enabled=item.enabled,
                )
            )
        return MaterializedDeployments(deployments=tuple(deployments), created_ids=tuple(created_ids))

    async def _commit(self, config: PoolConfig) -> str | None:
        try:
            save_pool_config(path=self._config_path, config=config)
            await self._scheduler.reconfigure(config)
        except (OSError, ValueError):
            return "号池配置保存失败"
        return None

    async def _rollback_created(self, deployment_ids: tuple[str, ...]) -> None:
        await asyncio.gather(*(self._admin.delete_deployment(deployment_id) for deployment_id in deployment_ids))

    async def _delete_deployments(self, deployment_ids: tuple[str, ...]) -> int:
        results: Final = await asyncio.gather(
            *(self._admin.delete_deployment(deployment_id) for deployment_id in deployment_ids)
        )
        return sum(result is not None for result in results)


@dataclass(frozen=True, slots=True)
class MaterializedDeployments:
    deployments: tuple[DeploymentConfig, ...]
    created_ids: tuple[str, ...]


def _account_config(request: AccountMutation, deployments: tuple[DeploymentConfig, ...]) -> AccountConfig:
    return AccountConfig(
        id=request.id,
        display_name=request.display_name,
        provider=request.provider,
        group=request.group,
        base_url_display=request.base_url_display,
        enabled=request.enabled,
        max_concurrency=request.max_concurrency,
        priority=request.priority,
        weight=request.weight,
        quotas=request.quotas,
        deployments=deployments,
    )


def _deployment_count(content: bytes) -> int | None:
    try:
        payload: Final = _JSON_OBJECT.validate_json(content)
        data: Final = payload.get("data")
        return len(_OBJECT_LIST.validate_python(data)) if isinstance(data, list) else None
    except ValidationError:
        return None


def _deployment_id(payload: Mapping[str, object]) -> str | None:
    direct: Final = payload.get("model_id")
    if isinstance(direct, str):
        return direct
    model_info: Final = _mapping(payload.get("model_info"))
    if model_info is None:
        return None
    nested: Final = model_info.get("id")
    return nested if isinstance(nested, str) else None


def _mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return cast(Mapping[str, object], value)
