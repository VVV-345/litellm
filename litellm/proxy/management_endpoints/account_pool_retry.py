"""本模块定义同卡重试预算、退避和输出前的流式缓冲，不执行账号切换或持久化。"""

from __future__ import annotations

import asyncio
import io
import math
import random
from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Final

from litellm.proxy.management_endpoints.account_pool_stream import EventStream


def replay_safe(payload: Mapping[str, object]) -> bool:
    return not any(payload.get(key) for key in ("previous_response_id", "conversation", "tools", "background"))


def retry_after(headers: Mapping[str, str]) -> int:
    raw: Final = headers.get("retry-after")
    try:
        seconds: Final = (
            float(raw)
            if raw is not None and raw.strip().replace(".", "", 1).isdigit()
            else (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds()
            if raw
            else float(headers.get("retry-after-ms", "0")) / 1000
        )
        return min(86400, max(0, math.ceil(seconds))) if math.isfinite(seconds) else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def backoff_seconds(milliseconds: int, same_card_attempt: int) -> float:
    return min(60, milliseconds / 1000 * math.pow(2.0, same_card_attempt - 1)) * random.uniform(0.8, 1.0)


class StreamBootstrap:
    def __init__(self, chunks: AsyncIterator[bytes]) -> None:
        self.chunks: Final = chunks
        self.state: Final = EventStream()
        self.buffer: Final = io.BytesIO()
        self.pending: asyncio.Task[bytes] | None = None
        self.overflow: bytes = b""

    def capture(self, chunk: bytes) -> None:
        remaining: Final = 65536 - self.buffer.tell()
        prefix: Final = chunk[:remaining]
        self.overflow = chunk[remaining:]
        self.buffer.write(prefix)
        for frame in self.state.feed(prefix):
            self.state.observe(frame)
            if self.state.terminal:
                break

    async def next_chunk(self) -> bytes:
        return await anext(self.chunks)

    def capture_pending(self) -> bool:
        if self.pending is None:
            return False
        try:
            chunk: Final = self.pending.result()
        except StopAsyncIteration:
            self.pending = None
            final: Final = self.state.finish()
            if final is not None:
                self.state.observe(final)
            if not self.state.terminal:
                self.state.failed = True
                self.state.error_code = "stream_interrupted"
            return False
        self.pending = None
        self.capture(chunk)
        return True

    async def prepare(self) -> None:
        deadline: Final = asyncio.get_running_loop().time() + 15
        while self.buffer.tell() < 65536 and not self.state.meaningful and not self.state.terminal:
            self.pending = asyncio.create_task(self.next_chunk())
            done, _ = await asyncio.wait((self.pending,), timeout=max(0, deadline - asyncio.get_running_loop().time()))
            if not done:
                return
            if not self.capture_pending():
                return

    async def replay(self) -> AsyncIterator[bytes]:
        if self.buffer.tell():
            yield self.buffer.getvalue()
        if self.overflow:
            yield self.overflow
        if self.pending is not None:
            try:
                yield await self.pending
            except StopAsyncIteration:
                return
            self.pending = None
        async for chunk in self.chunks:
            yield chunk

    async def close(self) -> None:
        if self.pending is not None:
            self.pending.cancel()
            await asyncio.gather(self.pending, return_exceptions=True)
