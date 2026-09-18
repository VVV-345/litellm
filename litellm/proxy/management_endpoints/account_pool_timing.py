"""记录同一请求各阶段耗时，不保存密钥、会话内容或上游地址。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Final
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from litellm._logging import verbose_proxy_logger


class TimingPhase(BaseModel):
    segment_id: UUID = Field(default_factory=uuid4)
    attempt: int
    phase: str
    duration_ms: float
    status: int


_pending: Final[set[asyncio.Task[None]]] = set()


async def _save(request_id: UUID, phases: tuple[TimingPhase, ...]) -> None:
    from litellm.proxy.management_endpoints.account_pool_full_logs import full_log_store

    try:
        await asyncio.to_thread(full_log_store().save_timing, request_id, phases)
    except Exception:
        verbose_proxy_logger.warning("Request timing persistence failed: request_id=%s", request_id)


class RequestTiming:
    def __init__(self, request_id: UUID, attempt: int = 0, started: float | None = None) -> None:
        self.request_id: Final = request_id
        self.segment_id: Final = uuid4()
        self.attempt: Final = attempt
        self.started: Final = time.perf_counter() if started is None else started
        self.phases: tuple[tuple[str, float], ...] = ()

    @contextmanager
    def phase(self, name: str) -> Generator[None]:
        started: Final = time.perf_counter()
        try:
            yield
        finally:
            self.phases = (*self.phases, (name, round((time.perf_counter() - started) * 1000, 2)))

    def report(self, status: int) -> None:
        verbose_proxy_logger.info(
            "account_pool_timing %s",
            json.dumps(
                {
                    "request_id": str(self.request_id),
                    "attempt": self.attempt,
                    "status": status,
                    "total_ms": round((time.perf_counter() - self.started) * 1000, 2),
                    "phases_ms": dict(self.phases),
                },
                separators=(",", ":"),
            ),
        )
        if len(_pending) >= 256:
            return
        try:
            loop: Final = asyncio.get_running_loop()
        except RuntimeError:
            return
        phases: Final = tuple(
            TimingPhase(
                segment_id=self.segment_id, attempt=self.attempt, phase=name, duration_ms=duration, status=status
            )
            for name, duration in (
                self.phases or (("ingress_total", round((time.perf_counter() - self.started) * 1000, 2)),)
            )
        )
        task: Final = loop.create_task(_save(self.request_id, phases))
        _pending.add(task)
        task.add_done_callback(_pending.discard)
