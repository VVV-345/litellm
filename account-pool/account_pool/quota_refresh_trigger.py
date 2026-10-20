"""合并额度失败事件，以有界后台工作器刷新单卡，不阻塞请求。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Final
from uuid import UUID

_LOGGER: Final = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Card:
    due: float = 0
    failures: int = 0
    pending: bool = True
    running: bool = False


class QuotaRefreshTrigger:
    def __init__(
        self,
        refresh: Callable[[UUID], Awaitable[bool]],
        max_concurrency: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        refresh_timeout_seconds: float = 60,
    ) -> None:
        self._refresh: Final = refresh
        self._concurrency: Final = max(1, max_concurrency)
        self._clock: Final = clock
        self._timeout: Final = refresh_timeout_seconds
        self._cards: Final[dict[UUID, _Card]] = {}
        self._wake: Final = asyncio.Event()
        self._overflow_logged = False

    def submit(self, card_id: UUID) -> None:
        current: Final = self._cards.get(card_id)
        if current is None and len(self._cards) >= 256:
            expired: Final = next(
                (key for key, value in self._cards.items()
                 if not value.pending and not value.running and value.due <= self._clock()),
                None,
            )
            if expired is None:
                if not self._overflow_logged:
                    _LOGGER.warning("Event quota refresh queue full; scheduled collection will refresh skipped cards")
                    self._overflow_logged = True
                return
            del self._cards[expired]
            self._overflow_logged = False
        self._cards[card_id] = replace(current, pending=True) if current else _Card()
        self._wake.set()

    async def _worker(self) -> None:
        while True:
            await self._step()

    async def _step(self) -> None:
        self._wake.clear()
        now: Final = self._clock()
        available: Final = tuple(
            (key, value) for key, value in self._cards.items() if value.pending and not value.running
        )
        selected: Final = next(((key, value) for key, value in available if value.due <= now), None)
        if selected is None:
            delay: Final = min((value.due - now for _, value in available), default=None)
            try:
                async with asyncio.timeout(delay):
                    await self._wake.wait()
            except TimeoutError:
                pass
            return
        card_id, state = selected
        # 同事件循环内先占用，再 await，多个工作器不能同时领取同一卡片。
        self._cards[card_id] = replace(state, pending=False, running=True)
        succeeded: Final = await self._run_refresh(card_id)
        failures: Final = 0 if succeeded else min(state.failures + 1, 4)
        self._cards[card_id] = replace(
            self._cards[card_id],
            running=False,
            failures=failures,
            due=self._clock() + (60, 60, 120, 240, 300)[failures],
        )
        self._wake.set()

    async def _run_refresh(self, card_id: UUID) -> bool:
        try:
            async with asyncio.timeout(self._timeout):
                return await self._refresh(card_id)
        except Exception as error:
            _LOGGER.warning("Event quota refresh failed: %s", type(error).__name__)
            return False

    async def run_until_cancelled(self, stopped: asyncio.Event) -> None:
        workers: Final = tuple(asyncio.create_task(self._worker()) for _ in range(self._concurrency))
        try:
            await stopped.wait()
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
