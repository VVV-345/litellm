"""号池控制面健康度：连续故障时快速拒绝新请求，不缓存解析结果也不接管调度。"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from enum import Enum
from typing import Final

import httpx

from litellm.proxy.management_endpoints.account_pool_gateway_client import ControlError, GatewayControl
from litellm.proxy.management_endpoints.account_pool_gateway_contracts import (
    AcquireRejected,
    AcquireRequest,
    FinishRequest,
    Lease,
    Resolution,
    ResolveRequest,
)


class ControlState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


def control_failure_is_outage(error: BaseException) -> bool:
    if isinstance(error, ControlError):
        return error.counts_as_outage
    return isinstance(error, (httpx.TransportError, TimeoutError, OSError))


class ControlHealth:
    """只统计连接失败、超时和明确控制面故障；权限与配置错误说明控制面可达。"""

    def __init__(
        self,
        *,
        failures: int = 3,
        open_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold: Final = max(1, failures)
        self._open_seconds: Final = max(1.0, open_seconds)
        self._clock: Final = clock
        self._consecutive = 0
        self._opened_at: float | None = None
        self._probe_started_at: float | None = None

    @property
    def state(self) -> ControlState:
        if self._opened_at is None:
            return ControlState.CLOSED
        return ControlState.HALF_OPEN if self._clock() - self._opened_at >= self._open_seconds else ControlState.OPEN

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive

    def permits(self) -> bool:
        state: Final = self.state
        if state is ControlState.CLOSED:
            return True
        if state is ControlState.OPEN:
            return False
        # 半开只放行一个探测请求，探测自身也要有上限，否则走失的探针会永久卡住恢复。
        if self._probe_started_at is not None and self._clock() - self._probe_started_at < self._open_seconds:
            return False
        self._probe_started_at = self._clock()
        return True

    def retry_after_seconds(self) -> int:
        if self._opened_at is None:
            return 0
        return max(1, math.ceil(self._open_seconds - (self._clock() - self._opened_at)))

    def succeeded(self) -> None:
        self._consecutive = 0
        self._opened_at = None
        self._probe_started_at = None

    def failed(self, *, outage: bool) -> None:
        if not outage:
            self.succeeded()
            return
        self._probe_started_at = None
        self._consecutive += 1
        if self._opened_at is not None or self._consecutive >= self._threshold:
            self._opened_at = self._clock()

    def wrap(self, control: GatewayControl) -> GatewayControl:
        return control if isinstance(control, HealthControl) else HealthControl(control, self)


class HealthControl:
    """把 resolve 与 acquire 的结果计入健康度；finish 是尽力而为的上报，不参与熔断。"""

    def __init__(self, control: GatewayControl, health: ControlHealth) -> None:
        self._control: Final = control
        self._health: Final = health

    async def resolve(self, request: ResolveRequest) -> Resolution:
        with self._observe():
            return await self._control.resolve(request)

    async def acquire(self, request: AcquireRequest) -> Lease | AcquireRejected:
        with self._observe():
            return await self._control.acquire(request)

    async def finish(self, request: FinishRequest) -> None:
        await self._control.finish(request)

    @contextmanager
    def _observe(self) -> Generator[None, None, None]:
        try:
            yield
        except BaseException as error:
            self._health.failed(outage=control_failure_is_outage(error))
            raise
        else:
            self._health.succeeded()
