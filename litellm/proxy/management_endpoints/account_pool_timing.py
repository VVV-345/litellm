"""记录同一请求各阶段耗时，不保存密钥、会话内容或上游地址。"""

from __future__ import annotations

import json
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Final
from uuid import UUID

from litellm._logging import verbose_proxy_logger


class RequestTiming:
    def __init__(self, request_id: UUID, attempt: int = 0, started: float | None = None) -> None:
        self.request_id: Final = request_id
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
