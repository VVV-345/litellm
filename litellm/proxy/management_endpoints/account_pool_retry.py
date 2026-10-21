"""本模块定义同卡重试预算、退避和输出前的流式缓冲，不执行账号切换或持久化。"""

from __future__ import annotations

import asyncio
import io
import math
import random
from collections.abc import AsyncIterator, Awaitable, Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from types import MappingProxyType
from typing import Final, TypeVar, cast
from uuid import UUID

from pydantic import JsonValue, TypeAdapter
from starlette.requests import ClientDisconnect, Request
from starlette.types import Message, Send

from litellm.litellm_core_utils.signature_recovery import client_tool, signature_recovery_reason
from litellm.proxy.common_utils.sse_keepalive import SSE_COMMENT_PING_BYTES, coerce_keepalive_interval
from litellm.proxy.management_endpoints.account_pool_failover import MAX_RETRY_DELAY_SECONDS
from litellm.proxy.management_endpoints.account_pool_stream import EventStream

_RESULT: Final = TypeVar("_RESULT")
_ERROR: Final = TypeAdapter(dict[str, JsonValue])
_STATUS: Final = TypeAdapter(int)
_HEADERS: Final = TypeAdapter(tuple[tuple[bytes, bytes], ...])
_BODY: Final = TypeAdapter(bytes)


def same_card_replay_safe(payload: Mapping[str, JsonValue]) -> bool:
    if replay_safe(payload):
        return True
    if any(payload.get(key) for key in ("previous_response_id", "conversation", "background", "functions")):
        return False
    history: Final = payload.get("messages", payload.get("input"))
    if isinstance(history, list) and history:
        declared: Final = payload.get("tools")
        if isinstance(declared, list) and not all(client_tool(tool) for tool in declared):
            return False
        return signature_recovery_reason(payload) is None
    tools: Final = payload.get("tools")
    return (
        (history is None or isinstance(history, str))
        and (tools is None or isinstance(tools, list) and all(client_tool(tool) for tool in tools))
    )


def failover_rebuildable(payload: Mapping[str, JsonValue]) -> bool:
    from litellm.proxy.management_endpoints.account_pool_session import has_signed_history

    if any(payload.get(key) for key in ("previous_response_id", "conversation", "background", "functions")):
        return False
    if has_server_reference(payload):
        return False
    history: Final = payload.get("messages", payload.get("input"))
    if isinstance(history, list) and history:
        return signature_recovery_reason(payload) is None
    tools: Final = payload.get("tools")
    return (
        (history is None or isinstance(history, str))
        and (tools is None or isinstance(tools, list) and all(client_tool(tool) for tool in tools))
        and not has_signed_history(dict(payload))
    )


class RetryStream:
    def __init__(self, request: Request, send: Send, request_id: UUID) -> None:
        self.request: Final = request
        self.send: Final = send
        self.request_id: Final = request_id
        self.started = False
        self.committed = False
        self.streaming = True
        self.status = 200
        self.state: Final = EventStream(
            responses_api=request.url.path == "/v1/responses", anthropic_messages=request.url.path == "/v1/messages"
        )

    async def emit(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.committed = True
            self.status = _STATUS.validate_python(message["status"])
            self.streaming = any(
                name.lower() == b"content-type" and value.startswith(b"text/event-stream")
                for name, value in _HEADERS.validate_python(message.get("headers", ()))
            )
            if self.started:
                return
            self.started = True
            self.streaming = True
        elif message["type"] == "http.response.body" and not self.streaming:
            payload: Final = _ERROR.validate_json(_BODY.validate_python(message.get("body", b"{}")))
            error: Final = payload.get("error", payload)
            outbound: Final[Message] = {
                "type": "http.response.body", "more_body": False,
                "body": self.state.error_frame(self.state.error_envelope(_ERROR.validate_python(MappingProxyType({
                    **(error if isinstance(error, dict) else {"message": str(error)}),
                    "status_code": self.status,
                })))),
            }
            await self.send(outbound)
            return
        await self.send(message)

    async def ping(self) -> None:
        if self.committed:
            return
        if not self.started:
            self.started = True
            start: Final[Message] = {
                "type": "http.response.start", "status": 200,
                "headers": (
                    (b"content-type", b"text/event-stream"), (b"cache-control", b"no-store"),
                    (b"x-accel-buffering", b"no"), (b"x-request-id", str(self.request_id).encode()),
                ),
            }
            await self.send(start)
        if not self.committed:
            body: Final[Message] = {"type": "http.response.body", "body": SSE_COMMENT_PING_BYTES, "more_body": True}
            await self.send(body)

    async def run(self, operation: Awaitable[_RESULT], interval: float | None) -> _RESULT:
        pending: Final = asyncio.ensure_future(operation)
        seconds: Final = coerce_keepalive_interval(10 if interval is None else interval)
        try:
            while not pending.done():
                done, _ = await asyncio.wait((pending,), timeout=seconds or 1)
                if done:
                    break
                if self.committed:
                    return await pending
                if await self.request.is_disconnected():
                    raise ClientDisconnect
                if seconds is not None and not getattr(self.request.state, "account_pool_standard_accounting", False):
                    await self.ping()
            return pending.result()
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


def replay_safe(payload: Mapping[str, object]) -> bool:
    from litellm.proxy.management_endpoints.account_pool_session import has_signed_history

    if has_signed_history(dict(payload)):
        return False
    if has_server_reference(payload):
        return False
    if any(payload.get(key) for key in ("previous_response_id", "conversation", "tools", "functions", "background")):
        return False
    inputs: Final = payload.get("input")
    if isinstance(inputs, list) and any(
        isinstance(item, Mapping)
        and (
            cast(Mapping[str, object], item).get("encrypted_content")
            or cast(Mapping[str, object], item).get("type")
            in ("function_call", "function_call_output", "custom_tool_call", "custom_tool_call_output", "item_reference")
        )
        for item in cast(list[object], inputs)
    ):
        return False
    messages: Final = payload.get("messages")
    return not isinstance(messages, list) or not any(
        isinstance(message, Mapping)
        and (
            cast(Mapping[str, object], message).get("tool_calls")
            or cast(Mapping[str, object], message).get("function_call")
            or cast(Mapping[str, object], message).get("role") in ("tool", "function")
            or isinstance(content := cast(Mapping[str, object], message).get("content"), list)
            and any(isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result") for block in content)
        )
        for message in cast(list[object], messages)
    )


def has_server_reference(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(value.get(key) for key in ("file_id", "container_id")) or any(
            has_server_reference(item) for item in value.values()
        )
    return isinstance(value, list) and any(has_server_reference(item) for item in value)


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
    return min(MAX_RETRY_DELAY_SECONDS, milliseconds / 1000 * math.pow(2.0, same_card_attempt - 1)) * random.uniform(
        0.8, 1.0
    )


class StreamBootstrap:
    def __init__(
        self, chunks: AsyncIterator[bytes], *, responses_api: bool = False, anthropic_messages: bool = False
    ) -> None:
        self.chunks: Final = chunks
        self.state: Final = EventStream(responses_api=responses_api, anthropic_messages=anthropic_messages)
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

    async def prepare(self, deadline: float = float("inf")) -> None:
        while self.buffer.tell() < 65536 and not self.state.meaningful and not self.state.terminal:
            self.pending = asyncio.create_task(self.next_chunk())
            done, _ = await asyncio.wait((self.pending,), timeout=max(0, deadline - asyncio.get_running_loop().time()))
            if not done:
                raise TimeoutError("Pre-output retry deadline exhausted")
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
