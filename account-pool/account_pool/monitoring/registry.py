"""以不可变快照记录后台 Worker 的启动、周期结果、连续失败和停止状态。"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Final, Protocol

from pydantic import AwareDatetime

from account_pool.monitoring.models import WorkerName, WorkerState, WorkerStateList, WorkerStatus

Clock = Callable[[], AwareDatetime]


@dataclass(frozen=True, slots=True)
class WorkerRegistration:
    worker: WorkerName
    enabled: bool
    expected_interval_seconds: float


class WorkerMonitor(Protocol):
    def started(self, worker: WorkerName) -> None: ...

    def cycle_started(self, worker: WorkerName) -> None: ...

    def succeeded(self, worker: WorkerName, duration_seconds: float) -> None: ...

    def failed(self, worker: WorkerName, duration_seconds: float) -> None: ...

    def stopped(self, worker: WorkerName) -> None: ...


class NoopWorkerMonitor:
    def started(self, worker: WorkerName) -> None:
        del worker

    def cycle_started(self, worker: WorkerName) -> None:
        del worker

    def succeeded(self, worker: WorkerName, duration_seconds: float) -> None:
        del worker, duration_seconds

    def failed(self, worker: WorkerName, duration_seconds: float) -> None:
        del worker, duration_seconds

    def stopped(self, worker: WorkerName) -> None:
        del worker


def utc_now() -> AwareDatetime:
    return datetime.now(UTC)


class WorkerMonitorRegistry:
    def __init__(self, registrations: Iterable[WorkerRegistration], clock: Clock = utc_now) -> None:
        resolved: Final = tuple(registrations)
        by_name: Final = {registration.worker: registration for registration in resolved}
        if len(by_name) != len(resolved):
            raise ValueError("worker registrations must be unique")
        if any(registration.expected_interval_seconds <= 0 for registration in resolved):
            raise ValueError("worker intervals must be positive")
        self._clock: Final = clock
        self._registrations: Final = MappingProxyType(by_name)
        self._states = MappingProxyType(
            {
                registration.worker: _initial_state(registration)
                for registration in sorted(resolved, key=lambda item: item.worker.value)
            }
        )

    def started(self, worker: WorkerName) -> None:
        current: Final = self._state(worker)
        self._replace(
            current.model_copy(
                update={
                    "running": True,
                    "status": WorkerStatus.STARTING,
                    "process_started_at": self._clock(),
                }
            )
        )

    def cycle_started(self, worker: WorkerName) -> None:
        current: Final = self._state(worker)
        self._replace(
            current.model_copy(
                update={
                    "run_count": current.run_count + 1,
                    "last_cycle_started_at": self._clock(),
                }
            )
        )

    def succeeded(self, worker: WorkerName, duration_seconds: float) -> None:
        current: Final = self._state(worker)
        self._replace(
            current.model_copy(
                update={
                    "status": WorkerStatus.HEALTHY,
                    "success_count": current.success_count + 1,
                    "consecutive_failures": 0,
                    "last_success_at": self._clock(),
                    "last_duration_seconds": max(duration_seconds, 0),
                }
            )
        )

    def failed(self, worker: WorkerName, duration_seconds: float) -> None:
        current: Final = self._state(worker)
        self._replace(
            current.model_copy(
                update={
                    "status": WorkerStatus.DEGRADED,
                    "failure_count": current.failure_count + 1,
                    "consecutive_failures": current.consecutive_failures + 1,
                    "last_failure_at": self._clock(),
                    "last_duration_seconds": max(duration_seconds, 0),
                }
            )
        )

    def stopped(self, worker: WorkerName) -> None:
        current: Final = self._state(worker)
        self._replace(current.model_copy(update={"running": False, "status": WorkerStatus.STOPPED}))

    def snapshot(self) -> WorkerStateList:
        observed_at: Final = self._clock()
        return WorkerStateList(
            workers=tuple(_observed_state(state, observed_at) for state in self._states.values()),
            observed_at=observed_at,
        )

    def _state(self, worker: WorkerName) -> WorkerState:
        state: Final = self._states.get(worker)
        if state is None:
            raise ValueError(f"worker is not registered: {worker.value}")
        return state

    def _replace(self, state: WorkerState) -> None:
        self._states = MappingProxyType({**self._states, state.worker: state})


def _initial_state(registration: WorkerRegistration) -> WorkerState:
    return WorkerState(
        worker=registration.worker,
        enabled=registration.enabled,
        running=False,
        status=WorkerStatus.STOPPED if registration.enabled else WorkerStatus.DISABLED,
        expected_interval_seconds=registration.expected_interval_seconds,
        run_count=0,
        success_count=0,
        failure_count=0,
        consecutive_failures=0,
    )


def _observed_state(state: WorkerState, observed_at: AwareDatetime) -> WorkerState:
    if not state.enabled:
        return state.model_copy(update={"status": WorkerStatus.DISABLED})
    if not state.running:
        return state
    references: Final = tuple(
        item
        for item in (
            state.process_started_at,
            state.last_cycle_started_at,
            state.last_success_at,
            state.last_failure_at,
        )
        if item is not None
    )
    reference: Final = max(references, default=None)
    stale: Final = (
        reference is not None
        and (observed_at - reference).total_seconds() > state.expected_interval_seconds * 2
    )
    return state.model_copy(update={"status": WorkerStatus.STALLED}) if stale else state
