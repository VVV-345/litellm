"""声明事件归档候选读取和精确删除的持久化边界。"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import AwareDatetime

from account_pool.retention.models import RetentionBatch, RetentionFailure, RetentionScope


class RetentionRepository(Protocol):
    async def load_oldest_month(
        self,
        *,
        scope: RetentionScope,
        before: AwareDatetime,
        limit: int,
    ) -> RetentionBatch | RetentionFailure | None: ...

    async def delete_archived(self, event_ids: tuple[UUID, ...]) -> int | RetentionFailure: ...
