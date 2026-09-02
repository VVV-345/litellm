"""本模块初始化 Router 缓存客户端，并按号池环境共享总并发信号量。"""

import asyncio
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

from litellm.utils import calculate_max_parallel_requests

if TYPE_CHECKING:
    from litellm.router import Router as _Router

    LitellmRouter = _Router
else:
    LitellmRouter = Any


class AccountPoolSemaphore(asyncio.Semaphore):
    def __init__(self, value: int) -> None:
        super().__init__(value)
        self._account_pool_limit = value

    def update_limit(self, value: int) -> None:
        if value == self._account_pool_limit:
            return
        # 通过 permit debt 收缩额度，已进入的请求完成前不会给新请求额外槽位
        self._value += value - self._account_pool_limit  # rebind-ok: asyncio semaphore tracks available permits
        self._account_pool_limit = value  # rebind-ok: environment concurrency configuration changed
        waiters: Final = self._waiters or ()
        for _ in range(len(waiters)):
            self._wake_up_next()

    def locked(self) -> bool:
        return self._value <= 0

    def _wake_up_next(self) -> None:
        if self._value > 0:
            super()._wake_up_next()


def account_pool_environment_id(model_info: object) -> str | None:
    if not isinstance(model_info, dict) or model_info.get("managed_by") != "account_pool":
        return None
    raw_environment_id: Final = model_info.get("account_pool_environment_id")
    if not isinstance(raw_environment_id, str):
        return None
    try:
        parsed_environment_id: Final = UUID(raw_environment_id)
    except ValueError:
        return None
    if str(parsed_environment_id) != raw_environment_id:
        return None
    return raw_environment_id


def account_pool_environment_limits(model_list: object) -> dict[str, int]:
    if not isinstance(model_list, list):
        return {}
    return {
        environment_id: max(limits)
        for environment_id, limits in _account_pool_environment_limit_entries(model_list).items()
    }


def _account_pool_environment_limit_entries(model_list: list[object]) -> dict[str, tuple[int, ...]]:
    entries: dict[str, tuple[int, ...]] = {}
    for deployment in model_list:
        if not isinstance(deployment, dict):
            continue
        environment_id: Final = account_pool_environment_id(deployment.get("model_info"))
        litellm_params: Final = deployment.get("litellm_params")
        if environment_id is None or not isinstance(litellm_params, dict):
            continue
        limit: Final = litellm_params.get("max_parallel_requests")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            continue
        entries[environment_id] = (*entries.get(environment_id, ()), limit)
    return entries


class InitalizeCachedClient:
    @staticmethod
    def set_max_parallel_requests_client(litellm_router_instance: LitellmRouter, model: dict):
        litellm_params: Final = model.get("litellm_params", {})
        model_id: Final = model["model_info"]["id"]
        model_info: Final = model.get("model_info", {})
        environment_id: Final = account_pool_environment_id(model_info)
        environment_limits: Final = account_pool_environment_limits(litellm_router_instance.model_list)
        rpm: Final = litellm_params.get("rpm", None)
        tpm: Final = litellm_params.get("tpm", None)
        max_parallel_requests: Final = litellm_params.get("max_parallel_requests", None)
        calculated_max_parallel_requests: Final = calculate_max_parallel_requests(
            rpm=rpm,
            max_parallel_requests=max_parallel_requests,
            tpm=tpm,
            default_max_parallel_requests=litellm_router_instance.default_max_parallel_requests,
        )
        if calculated_max_parallel_requests:
            effective_limit: Final = (
                environment_limits.get(environment_id, calculated_max_parallel_requests)
                if environment_id is not None
                else calculated_max_parallel_requests
            )
            cache_key: Final = max_parallel_requests_cache_key(model_id, environment_id)
            existing: Final = litellm_router_instance.cache.get_cache(key=cache_key, local_only=True)
            if isinstance(existing, AccountPoolSemaphore):
                existing.update_limit(effective_limit)
                return
            if existing is not None:
                return
            # 普通部署按 ID 隔离，号池部署用环境共享且可原地调整额度
            semaphore: Final = (
                AccountPoolSemaphore(effective_limit)
                if environment_id is not None
                else asyncio.Semaphore(effective_limit)
            )
            litellm_router_instance.cache.set_cache(
                key=cache_key,
                value=semaphore,
                local_only=True,
            )


def max_parallel_requests_cache_key(model_id: str, environment_id: str | None = None) -> str:
    """根据部署 ID 或账号池环境返回并发信号量缓存键。"""
    if environment_id is not None:
        return f"account_pool_environment:{environment_id}:max_parallel_requests_client"
    return f"{model_id}_max_parallel_requests_client"
