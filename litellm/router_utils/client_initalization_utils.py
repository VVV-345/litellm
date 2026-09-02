"""本模块初始化 Router 缓存客户端，并按号池环境共享总并发信号量。"""

import asyncio
import threading
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
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._owner_lock = threading.Lock()

    async def acquire(self) -> bool:
        loop: Final = asyncio.get_running_loop()
        with self._owner_lock:
            if self._owner_loop is None:
                self._owner_loop = loop
            elif self._owner_loop is not loop:
                raise RuntimeError("Account Pool concurrency limiter is owned by another event loop")
        return await super().acquire()

    def update_limit(self, value: int) -> None:
        with self._owner_lock:
            owner_loop: Final = self._owner_loop
        if owner_loop is None:
            self._apply_limit(value)
            return
        try:
            if asyncio.get_running_loop() is owner_loop:
                self._apply_limit(value)
                return
        except RuntimeError:
            pass
        if not owner_loop.is_running():
            raise RuntimeError("Account Pool concurrency limiter owner event loop is not running")
        completed = threading.Event()
        errors: list[BaseException] = []

        def apply_limit() -> None:
            try:
                self._apply_limit(value)
            except BaseException as error:
                errors.append(error)
            finally:
                completed.set()

        owner_loop.call_soon_threadsafe(apply_limit)
        completed.wait()
        if errors:
            raise errors[0]

    def _apply_limit(self, value: int) -> None:
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
    entries: Final = _account_pool_environment_limit_entries(model_list)
    inconsistent_environment_ids: Final = tuple(
        environment_id for environment_id, limits in entries.items() if len(frozenset(limits)) > 1
    )
    if inconsistent_environment_ids:
        raise ValueError(
            f"Account Pool environment has inconsistent concurrency limits: {', '.join(sorted(inconsistent_environment_ids))}"
        )
    return {environment_id: limits[0] for environment_id, limits in entries.items()}


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


class AccountPoolConcurrencyRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._semaphores: dict[str, AccountPoolSemaphore] = {}
        self._snapshot_limits: dict[str, int] = {}

    def update_snapshot(self, model_list: object) -> None:
        snapshot_limits: Final = account_pool_environment_limits(model_list)
        with self._lock:
            self._snapshot_limits = snapshot_limits  # rebind-ok: Router model snapshot replaced atomically
            for environment_id, semaphore in self._semaphores.items():
                limit: Final = snapshot_limits.get(environment_id)
                if limit is not None:
                    semaphore.update_limit(limit)

    def get_or_create(self, environment_id: str, fallback_limit: int) -> AccountPoolSemaphore:
        with self._lock:
            effective_limit: Final = self._snapshot_limits.get(environment_id, fallback_limit)
            existing: Final = self._semaphores.get(environment_id)
            if existing is not None:
                existing.update_limit(effective_limit)
                return existing
            semaphore: Final = AccountPoolSemaphore(effective_limit)
            self._semaphores[environment_id] = semaphore
            return semaphore


class InitalizeCachedClient:
    @staticmethod
    def set_max_parallel_requests_client(litellm_router_instance: LitellmRouter, model: dict):
        litellm_params: Final = model.get("litellm_params", {})
        model_id: Final = model["model_info"]["id"]
        model_info: Final = model.get("model_info", {})
        environment_id: Final = account_pool_environment_id(model_info)
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
            effective_limit: Final = calculated_max_parallel_requests
            cache_key: Final = max_parallel_requests_cache_key(model_id, environment_id)
            if environment_id is not None:
                semaphore: Final = litellm_router_instance._account_pool_concurrency_registry.get_or_create(
                    environment_id,
                    effective_limit,
                )
                litellm_router_instance.cache.set_cache(key=cache_key, value=semaphore, local_only=True)
                return
            existing: Final = litellm_router_instance.cache.get_cache(key=cache_key, local_only=True)
            if existing is not None:
                return
            semaphore: Final = asyncio.Semaphore(effective_limit)
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
