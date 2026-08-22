"""统一执行后台 Worker 周期，并上报启动、成功、失败和停止状态。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from account_pool.monitoring.models import WorkerName
from account_pool.monitoring.registry import WorkerMonitor

Cycle = Callable[[], Awaitable[object]]
CycleSuccess = Callable[[object], bool]
Sleep = Callable[[float], Awaitable[None]]
Monotonic = Callable[[], float]


def cycle_completed(_: object) -> bool:
    return True


async def run_worker_loop(
    *,
    worker: WorkerName,
    cycle: Cycle,
    interval_seconds: float,
    monitor: WorkerMonitor,
    logger: logging.Logger,
    failure_message: str,
    initial_delay: bool = False,
    sleep: Sleep = asyncio.sleep,
    monotonic: Monotonic = time.perf_counter,
    result_is_success: CycleSuccess = cycle_completed,
) -> None:
    if interval_seconds <= 0:
        raise ValueError("worker interval must be positive")
    monitor.started(worker)
    try:
        if initial_delay:
            await sleep(interval_seconds)
        while True:
            monitor.cycle_started(worker)
            started_at = monotonic()
            try:
                result = await cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                monitor.failed(worker, monotonic() - started_at)
                logger.error(failure_message)
            else:
                duration = monotonic() - started_at
                if result_is_success(result):
                    monitor.succeeded(worker, duration)
                else:
                    monitor.failed(worker, duration)
                    logger.error(failure_message)
            await sleep(interval_seconds)
    finally:
        monitor.stopped(worker)


async def run_monitored_service(
    *,
    worker: WorkerName,
    service: Cycle,
    monitor: WorkerMonitor,
    logger: logging.Logger,
    failure_message: str,
    monotonic: Monotonic = time.perf_counter,
) -> None:
    monitor.started(worker)
    started_at = monotonic()
    try:
        await service()
    except asyncio.CancelledError:
        raise
    except Exception:
        monitor.failed(worker, monotonic() - started_at)
        logger.error(failure_message)
        raise
    finally:
        monitor.stopped(worker)
