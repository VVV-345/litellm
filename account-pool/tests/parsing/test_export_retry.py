"""验证解析快照后台重试循环的批量边界、间隔和故障隔离。"""

import asyncio
from typing import Final

import pytest
from account_pool.parsing.export_retry import ParserExportRetryLoop
from account_pool.parsing.persistence import (
    ParserPersistenceFailure,
    ParserPersistenceFailureCode,
)
from account_pool.parsing.worker import (
    ParserRetryBatchFailure,
    ParserRetryBatchResult,
    ParserRetryBatchSuccess,
)


class RecordingRetrier:
    def __init__(self, result: ParserRetryBatchResult) -> None:
        self._result: Final = result
        self.limits: list[int] = []

    async def retry_exports(self, limit: int = 25) -> ParserRetryBatchResult:
        self.limits.append(limit)
        return self._result


class StopAfterFirstInterval:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        raise asyncio.CancelledError


@pytest.mark.parametrize(
    "result",
    (
        ParserRetryBatchSuccess(),
        ParserRetryBatchFailure(
            failure=ParserPersistenceFailure(
                code=ParserPersistenceFailureCode.DATABASE_UNAVAILABLE,
                retryable=True,
            )
        ),
    ),
)
async def test_retry_loop_runs_bounded_cycle_then_waits(result: ParserRetryBatchResult) -> None:
    retrier: Final = RecordingRetrier(result)
    sleep: Final = StopAfterFirstInterval()
    loop: Final = ParserExportRetryLoop(retrier, interval_seconds=17, batch_size=9, sleep=sleep)

    with pytest.raises(asyncio.CancelledError):
        await loop.run()

    assert retrier.limits == [9]
    assert sleep.delays == [17]


@pytest.mark.parametrize(
    ("interval_seconds", "batch_size"),
    ((0, 25), (-1, 25), (30, 0), (30, 101)),
)
def test_retry_loop_rejects_invalid_bounds(interval_seconds: float, batch_size: int) -> None:
    with pytest.raises(ValueError):
        ParserExportRetryLoop(
            RecordingRetrier(ParserRetryBatchSuccess()),
            interval_seconds=interval_seconds,
            batch_size=batch_size,
        )
