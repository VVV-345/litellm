"""定期重试已提交但尚未成功导出 JSON 快照的解析运行。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Final, Protocol

from account_pool.parsing.worker import (
    ParserRetryBatchFailure,
    ParserRetryBatchResult,
    ParserWorkFailure,
)

Sleep = Callable[[float], Awaitable[None]]
_LOGGER: Final = logging.getLogger(__name__)


class ParserExportRetrier(Protocol):
    async def retry_exports(self, limit: int = 25) -> ParserRetryBatchResult: ...


class ParserExportRetryManager(Protocol):
    async def run(self) -> None: ...


class ParserExportRetryLoop:
    def __init__(
        self,
        retrier: ParserExportRetrier,
        *,
        interval_seconds: float = 30,
        batch_size: int = 25,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if batch_size < 1 or batch_size > 100:
            raise ValueError("batch_size must be between 1 and 100")
        self._retrier: Final = retrier
        self._interval_seconds: Final = interval_seconds
        self._batch_size: Final = batch_size
        self._sleep: Final = sleep

    async def run(self) -> None:
        while True:
            try:
                await self._run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("parser snapshot export retry cycle crashed")
            await self._sleep(self._interval_seconds)

    async def _run_cycle(self) -> None:
        result: Final = await self._retrier.retry_exports(self._batch_size)
        _log_result(result)


def _log_result(result: ParserRetryBatchResult) -> None:
    if isinstance(result, ParserRetryBatchFailure):
        _LOGGER.warning(
            "parser snapshot export retry load failed: code=%s retryable=%s",
            result.failure.code,
            result.failure.retryable,
        )
        return
    failures: Final = tuple(item for item in result.outcomes if isinstance(item, ParserWorkFailure))
    if failures:
        _LOGGER.warning("parser snapshot export retry completed with %d worker failures", len(failures))
