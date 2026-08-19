"""定义持久化装饰层与内存、Redis 额度运行后端之间的最小交换契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from account_pool.models import AccountId, Lease
from account_pool.quota.runtime import RuntimeQuotaWindow


@dataclass(frozen=True, slots=True)
class QuotaBackendWindowState:
    account_id: AccountId
    window: RuntimeQuotaWindow
    reservation_expires_at: float | None = None


@dataclass(frozen=True, slots=True)
class QuotaBackendState:
    generation_id: UUID | None
    windows: tuple[QuotaBackendWindowState, ...]


class QuotaRuntimeBackend(Protocol):
    async def read_quota_generation(self) -> UUID | None: ...

    async def quota_backend_state(self, account_id: AccountId | None = None) -> QuotaBackendState | None: ...

    async def restore_quota_backend(
        self,
        generation_id: UUID,
        windows: tuple[QuotaBackendWindowState, ...],
    ) -> bool: ...

    async def set_quota_generation(self, generation_id: UUID | None) -> None: ...

    async def read_lease(self, lease_id: str) -> Lease | None: ...
