"""本模块提供管理员完整日志查询和独立清理接口，正文响应禁止浏览器缓存。"""

from __future__ import annotations

import asyncio
import os
import time
from functools import cache
from typing import Annotated, Final, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query, Response

from litellm.proxy.management_endpoints.account_pool_full_logs import (
    FullLogPage,
    FullLogQuery,
    FullLogRecord,
    FullLogStorageStats,
    full_log_store,
)
from litellm.proxy.management_endpoints.account_pool_management_models import (
    AccountPoolLogClearResult,
    AccountPoolSettingsView,
)


def create_full_log_router(*, prefix: str = "/full-logs") -> APIRouter:
    router: Final = APIRouter(prefix=prefix)

    async def _logs(query: Annotated[FullLogQuery, Query()], response: Response) -> FullLogPage:
        response.headers["Cache-Control"] = "no-store"
        return await asyncio.to_thread(full_log_store().query, query)

    async def _storage(response: Response) -> FullLogStorageStats:
        response.headers["Cache-Control"] = "no-store"
        return await asyncio.to_thread(full_log_store().storage)

    async def _clear(older_than_days: Literal["7", "14", "30", "45"] | None = None) -> AccountPoolLogClearResult:
        deleted: Final = await asyncio.to_thread(
            full_log_store().prune, int(older_than_days) if older_than_days else None
        )
        return AccountPoolLogClearResult(deleted=deleted)

    async def _detail(event_id: UUID, response: Response) -> FullLogRecord:
        response.headers["Cache-Control"] = "no-store"
        record: Final = await asyncio.to_thread(full_log_store().detail, event_id)
        if record is None:
            raise HTTPException(404, "完整日志未保存或已清理")
        return record

    router.add_api_route("", _logs, methods=["GET"])
    router.add_api_route("/storage", _storage, methods=["GET"])
    router.add_api_route("", _clear, methods=["DELETE"])
    router.add_api_route("/{event_id}", _detail, methods=["GET"])
    return router


class FullLogMaintenance:
    def __init__(self) -> None:
        self.next_check = 0.0

    async def run(self) -> None:
        if time.monotonic() < self.next_check:
            return
        self.next_check = time.monotonic() + 60
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response: Final = await client.get(
                os.getenv("ACCOUNT_POOL_MANAGER_URL", "http://account-pool:8091").rstrip("/") + "/api/settings",
                headers={"Authorization": "Bearer " + os.environ["ACCOUNT_POOL_MANAGER_TOKEN"]},
            )
            response.raise_for_status()
            settings: Final = AccountPoolSettingsView.model_validate_json(response.content)
        from litellm.proxy.management_endpoints.request_log_runtime import runtime_logging

        await asyncio.to_thread(runtime_logging.apply, settings.values)
        await asyncio.to_thread(full_log_store().prune, settings.values.full_log_retention_days, 1000)
        await asyncio.to_thread(full_log_store().limit_storage, settings.values.full_log_max_storage_mb)
        self.next_check = time.monotonic() + 60


@cache
def full_log_maintenance() -> FullLogMaintenance:
    return FullLogMaintenance()
