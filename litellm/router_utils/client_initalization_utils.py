"""本模块初始化 Router 缓存客户端，并按号池环境共享总并发信号量。"""

import asyncio
from typing import TYPE_CHECKING, Any, Final

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


class InitalizeCachedClient:
    @staticmethod
    def set_max_parallel_requests_client(litellm_router_instance: LitellmRouter, model: dict):
        litellm_params: Final = model.get("litellm_params", {})
        model_id: Final = model["model_info"]["id"]
        model_info: Final = model.get("model_info", {})
        environment_id: Final = model_info.get("account_pool_environment_id")
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
            cache_key: Final = max_parallel_requests_cache_key(model_id, environment_id)
            existing: Final = litellm_router_instance.cache.get_cache(key=cache_key, local_only=True)
            if isinstance(existing, AccountPoolSemaphore):
                existing.update_limit(calculated_max_parallel_requests)
                return
            if existing is not None:
                return
            # 普通部署按 ID 隔离，号池部署用环境共享且可原地调整额度
            semaphore: Final = (
                AccountPoolSemaphore(calculated_max_parallel_requests)
                if isinstance(environment_id, str) and environment_id
                else asyncio.Semaphore(calculated_max_parallel_requests)
            )
            litellm_router_instance.cache.set_cache(
                key=cache_key,
                value=semaphore,
                local_only=True,
            )


def max_parallel_requests_cache_key(model_id: str, environment_id: object = None) -> str:
    """根据部署 ID 或账号池环境返回并发信号量缓存键。"""
    if isinstance(environment_id, str) and environment_id:
        return f"account_pool_environment:{environment_id}:max_parallel_requests_client"
    return f"{model_id}_max_parallel_requests_client"
