"""按 Manager 操作记录故障代际；只有实际 I/O 领取探针，收尾上报不熔断。"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Final, Literal, TypeAlias

import httpx

from litellm.proxy.management_endpoints.account_pool_gateway_client import ControlError, GatewayControl
from litellm.proxy.management_endpoints.account_pool_gateway_contracts import (
    AcquireRejected,
    AcquireRequest,
    FinishRequest,
    Lease,
    LogStatusRequest,
    Resolution,
    ResolveRequest,
)

Operation: TypeAlias = Literal["resolve", "acquire"]


class ControlState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class Observation:
    operation: Operation
    generation: int
    probe: int | None = None


def control_failure_is_outage(error: BaseException) -> bool:
    if isinstance(error, ControlError):
        return error.counts_as_outage
    if isinstance(error, (httpx.PoolTimeout, httpx.LocalProtocolError, httpx.UnsupportedProtocol)):
        return False
    return isinstance(error, (httpx.TransportError, TimeoutError, ConnectionError))


class ControlHealth:
    def __init__(
        self, *, failures: int = 3, open_seconds: float = 30.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._threshold: Final = max(1, failures)
        self._open_seconds: Final = max(1.0, open_seconds)
        self._clock: Final = clock
        self._resolve_failures = 0
        self._acquire_failures = 0
        self._opened_at: float | None = None
        self._failed_operation: Operation = "resolve"
        self._generation = 0
        self._probe_serial = 0
        self._probe: int | None = None
        self._probe_started_at: float | None = None
        self._half_open_resolve_started_at: float | None = None

    @property
    def state(self) -> ControlState:
        if self._opened_at is None:
            return ControlState.CLOSED
        return ControlState.HALF_OPEN if self._clock() - self._opened_at >= self._open_seconds else ControlState.OPEN

    @property
    def consecutive_failures(self) -> int:
        return max(self._resolve_failures, self._acquire_failures)

    def available(self) -> bool:
        if self.state is ControlState.OPEN:
            return False
        return self._probe_started_at is None or self._clock() - self._probe_started_at >= self._open_seconds

    def begin(self, operation: Operation) -> Observation | None:
        if not self.available():
            return None
        if self.state is ControlState.HALF_OPEN:
            if operation == self._failed_operation:
                self._half_open_resolve_started_at = None
                self._probe_serial += 1
                self._probe = self._probe_serial
                self._probe_started_at = self._clock()
                return Observation(operation, self._generation, self._probe)
            # acquire 触发半开时仍需放行前置 resolve，但限制同时只放行一个探测请求，避免半开惊群。
            if (
                self._half_open_resolve_started_at is not None
                and self._clock() - self._half_open_resolve_started_at < self._open_seconds
            ):
                return None
            self._half_open_resolve_started_at = self._clock()
        return Observation(operation, self._generation)

    def permits(self) -> bool:
        return self.begin(self._failed_operation) is not None

    def retry_after_seconds(self) -> int:
        if self._opened_at is None:
            return 0
        return max(1, math.ceil(self._open_seconds - (self._clock() - self._opened_at)))

    def _current(self, observation: Observation) -> bool:
        return observation.generation == self._generation and (
            observation.probe is None or (
                observation.probe == self._probe
                and self._probe_started_at is not None
                and self._clock() - self._probe_started_at < self._open_seconds
            )
        )

    def abandon(self, observation: Observation) -> None:
        if self._current(observation):
            if observation.operation == "resolve":
                self._half_open_resolve_started_at = None
            if observation.probe is not None:
                self._probe = None
                self._probe_started_at = None

    def succeeded(self, observation: Observation | None = None) -> None:
        if observation is None:
            self._resolve_failures = 0
            self._acquire_failures = 0
            self._opened_at = None
            self._probe = None
            self._probe_started_at = None
            self._half_open_resolve_started_at = None
            return
        if not self._current(observation):
            return
        if observation.operation == "resolve":
            self._resolve_failures = 0
            self._half_open_resolve_started_at = None
        else:
            self._acquire_failures = 0
        if self._opened_at is not None and observation.probe is None:
            return
        self._probe = None
        self._probe_started_at = None
        self._half_open_resolve_started_at = None
        if self.consecutive_failures >= self._threshold:
            self._failed_operation = "resolve" if self._resolve_failures >= self._threshold else "acquire"
        else:
            self._opened_at = None

    def failed(self, *, outage: bool, observation: Observation | None = None) -> None:
        if observation is not None and not self._current(observation):
            return
        if not outage:
            self.succeeded(observation)
            return
        operation: Final = observation.operation if observation else "resolve"
        if operation == "resolve":
            self._resolve_failures += 1
        else:
            self._acquire_failures += 1
        if self._opened_at is not None or self.consecutive_failures >= self._threshold:
            self._generation += 1
            self._opened_at = self._clock()
            self._failed_operation = operation
            self._probe = None
            self._probe_started_at = None
            self._half_open_resolve_started_at = None

    def wrap(self, control: GatewayControl) -> GatewayControl:
        return control if isinstance(control, HealthControl) else HealthControl(control, self)


class HealthControl:
    def __init__(self, control: GatewayControl, health: ControlHealth) -> None:
        self._control: Final = control
        self._health: Final = health

    async def resolve(self, request: ResolveRequest) -> Resolution:
        async with self._observe("resolve"):
            return await self._control.resolve(request)

    async def acquire(self, request: AcquireRequest) -> Lease | AcquireRejected:
        async with self._observe("acquire"):
            return await self._control.acquire(request)

    async def finish(self, request: FinishRequest) -> None:
        await self._control.finish(request)

    async def log_status(self, request: LogStatusRequest) -> None:
        await self._control.log_status(request)

    @asynccontextmanager
    async def _observe(self, operation: Operation) -> AsyncGenerator[None, None]:
        observation: Final = self._health.begin(operation)
        if observation is None:
            raise ControlError(503, reason="circuit_open")
        try:
            async with asyncio.timeout(15):
                yield
        except BaseException as error:
            if control_failure_is_outage(error):
                self._health.failed(outage=True, observation=observation)
                if isinstance(error, TimeoutError):
                    raise ControlError(503) from error
            elif isinstance(error, ControlError) and error.reason != "configuration":
                self._health.succeeded(observation)
            else:
                self._health.abandon(observation)
            raise
        else:
            self._health.succeeded(observation)
