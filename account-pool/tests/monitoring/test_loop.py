"""验证后台周期执行器上报失败后继续运行，并在取消时标记停止。"""

import asyncio
import logging
from collections.abc import Callable
from typing import Final

import pytest
from account_pool.monitoring.loop import run_monitored_service, run_worker_loop
from account_pool.monitoring.models import WorkerName, WorkerStatus
from account_pool.monitoring.registry import WorkerMonitorRegistry, WorkerRegistration


class FailThenSucceed:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("sensitive upstream failure text")


class StopAfterTwoSleeps:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, delay: float) -> None:
        assert delay == 5
        self.calls += 1
        if self.calls == 2:
            raise asyncio.CancelledError


def _monotonic(values: tuple[float, ...]) -> Callable[[], float]:
    iterator: Final = iter(values)
    return lambda: next(iterator)


async def test_worker_loop_recovers_after_failed_cycle_and_stops_on_cancel(caplog: pytest.LogCaptureFixture) -> None:
    registry: Final = WorkerMonitorRegistry((WorkerRegistration(WorkerName.LEASE_REAPER, True, 5),))
    cycle: Final = FailThenSucceed()
    sleep: Final = StopAfterTwoSleeps()

    with caplog.at_level(logging.ERROR), pytest.raises(asyncio.CancelledError):
        await run_worker_loop(
            worker=WorkerName.LEASE_REAPER,
            cycle=cycle,
            interval_seconds=5,
            monitor=registry,
            logger=logging.getLogger(__name__),
            failure_message="lease cycle failed",
            sleep=sleep,
            monotonic=_monotonic((1.0, 1.25, 2.0, 2.5)),
        )

    state: Final = registry.snapshot().workers[0]
    assert cycle.calls == 2
    assert state.status == WorkerStatus.STOPPED
    assert state.run_count == 2
    assert state.success_count == 1
    assert state.failure_count == 1
    assert state.consecutive_failures == 0
    assert "lease cycle failed" in caplog.text
    assert "sensitive upstream failure text" not in caplog.text


async def test_monitored_service_reports_unexpected_exit_without_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry: Final = WorkerMonitorRegistry((WorkerRegistration(WorkerName.PUBLIC_METADATA, True, 30),))

    async def fail() -> None:
        raise RuntimeError("private provider response")

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="private provider response"):
        await run_monitored_service(
            worker=WorkerName.PUBLIC_METADATA,
            service=fail,
            monitor=registry,
            logger=logging.getLogger(__name__),
            failure_message="public metadata worker stopped",
            monotonic=_monotonic((1.0, 1.5)),
        )

    state: Final = registry.snapshot().workers[0]
    assert state.status == WorkerStatus.STOPPED
    assert state.failure_count == 1
    assert "public metadata worker stopped" in caplog.text
    assert "private provider response" not in caplog.text
