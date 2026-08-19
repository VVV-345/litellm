"""将 PostgreSQL 权威渠道目录安全投影到调度器和 Redis 运行态。"""

from typing import Final, Protocol

from account_pool.models import AccountSnapshot, PoolConfig


class AppliedCatalogConfig(Protocol):
    async def projected_config(self) -> PoolConfig: ...


class RuntimeScheduler(Protocol):
    async def reconfigure(self, config: PoolConfig) -> None: ...

    async def account_snapshots(self) -> tuple[AccountSnapshot, ...]: ...


class RuntimeProjector:
    def __init__(self, catalog: AppliedCatalogConfig, scheduler: RuntimeScheduler) -> None:
        self._catalog: Final = catalog
        self._scheduler: Final = scheduler

    async def project(self) -> PoolConfig:
        config: Final = await self._catalog.projected_config()
        await self._scheduler.reconfigure(config)
        return config

    async def inflight(self, account_id: str) -> int:
        snapshots: Final = await self._scheduler.account_snapshots()
        matched: Final = next((snapshot for snapshot in snapshots if snapshot.account_id == account_id), None)
        return 0 if matched is None else matched.inflight
