"""扫描自然到期额度并复用有界刷新队列；不兑换次数、不写认证文件。"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from uuid import UUID

from account_pool.domain import ChannelKind, EnvironmentRecord, EnvironmentStatus, SupplierKind, utc_now
from account_pool.quota_refresh_trigger import QuotaRefreshTrigger


def due_models(record: EnvironmentRecord, now: datetime) -> tuple[str, ...]:
    if (record.channel is not ChannelKind.CLIPROXYAPI or record.supplier is not SupplierKind.OPENAI_CODEX
        or not record.enabled or record.manual_cooldown or record.auth_file_disabled or record.configuration_pending
        or record.status not in (EnvironmentStatus.READY, EnvironmentStatus.COOLING_DOWN)):
        return ()
    account_due = any(w.remaining_percent <= 0 and w.resets_at is not None and w.resets_at <= now for w in record.quota.windows)
    fresh_available = (record.quota.source == "provider_api" and record.quota.observed_at is not None
                       and now - timedelta(minutes=5) <= record.quota.observed_at <= now
                       and bool(record.quota.windows) and all(w.remaining_percent > 0 for w in record.quota.windows))
    return tuple(model for model in record.enabled_models if account_due
                 or (fresh_available and record.automatic_cooldown)
                 or any(item.model == model and item.reason == "rate_limit" and (item.retry_at <= now or fresh_available) for item in record.model_cooldowns)
                 or any(item.model == model and any(w.remaining_percent <= 0 and w.resets_at is not None and w.resets_at <= now for w in item.quota.windows) for item in record.model_quotas))


class QuotaRecoveryScheduler:
    def __init__(self, records: Callable[[], Awaitable[tuple[EnvironmentRecord, ...]]],
                 recover: Callable[[UUID], Awaitable[bool]], max_concurrency: int,
                 *, clock: Callable[[], float] = time.monotonic) -> None:
        self._records = records
        self.trigger = QuotaRefreshTrigger(recover, max_concurrency, clock=clock, refresh_timeout_seconds=180)

    async def scan_once(self) -> None:
        now = utc_now()
        for record in await self._records():
            if due_models(record, now):
                self.trigger.submit(record.id)

    async def run_until_cancelled(self, stopped: asyncio.Event) -> None:
        worker = asyncio.create_task(self.trigger.run_until_cancelled(stopped))
        try:
            while not stopped.is_set():
                try:
                    await self.scan_once()
                except Exception as error:
                    logging.getLogger(__name__).warning("Quota recovery scan failed: %s", type(error).__name__)
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=30)
                except TimeoutError:
                    pass
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
