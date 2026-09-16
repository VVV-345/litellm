from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Final, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from account_pool.domain import utc_now
from account_pool.settings import AccountPoolSettingsRepository

T = TypeVar("T")


class QuotaRefreshStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    interval_minutes: int = Field(ge=5, le=60)
    running: bool = False
    last_started_at: datetime | None = None
    last_completed_at: datetime | None = None
    next_refresh_at: datetime | None = None
    last_failed_count: int | None = Field(default=None, ge=0)


class QuotaRefreshScheduler:
    def __init__(
        self,
        settings: AccountPoolSettingsRepository,
        refresh: Callable[[], Awaitable[tuple[object, ...]]],
    ) -> None:
        self._settings: Final = settings
        self._refresh: Final = refresh
        self._wake: Final = asyncio.Event()
        self._lock: Final = asyncio.Lock()
        self._running = False
        self._last_started_at: datetime | None = None
        self._last_completed_at: datetime | None = None
        self._next_refresh_at: datetime | None = None
        self._last_failed_count: int | None = None

    async def status(self) -> QuotaRefreshStatus:
        interval: Final = (await self._settings.get()).values.quota_refresh_interval_minutes
        return QuotaRefreshStatus(
            interval_minutes=interval,
            running=self._running,
            last_started_at=self._last_started_at,
            last_completed_at=self._last_completed_at,
            next_refresh_at=self._next_refresh_at,
            last_failed_count=self._last_failed_count,
        )

    def settings_changed(self) -> None:
        self._wake.set()

    async def track(self, operation: Callable[[], Awaitable[T]], failed_count: Callable[[T], int]) -> T:
        async with self._lock:
            self._running = True
            self._last_started_at = utc_now()
            try:
                result: Final = await operation()
            except Exception:
                self._last_completed_at = utc_now()
                self._last_failed_count = None
                self._running = False
                await self._schedule_next()
                raise
            self._last_completed_at = utc_now()
            self._last_failed_count = failed_count(result)
            self._running = False
            await self._schedule_next()
            return result

    async def run_until_cancelled(self, stopped: asyncio.Event) -> None:
        await self._schedule_next()
        while not stopped.is_set():
            now: Final = utc_now()
            timeout: Final = max(0.0, (self._next_refresh_at - now).total_seconds()) if self._next_refresh_at else 0.0
            stop_task: Final = asyncio.create_task(stopped.wait())
            wake_task: Final = asyncio.create_task(self._wake.wait())
            done, pending = await asyncio.wait(
                (stop_task, wake_task), timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if stop_task in done and stop_task.result():
                return
            if wake_task in done and wake_task.result():
                self._wake.clear()
                await self._schedule_next()
                continue
            await self.track(self._refresh, len)

    async def _schedule_next(self) -> None:
        interval: Final = (await self._settings.get()).values.quota_refresh_interval_minutes
        base: Final = self._last_completed_at or utc_now()
        self._next_refresh_at = base + timedelta(minutes=interval)
