"""本模块增量解析有界 SSE 帧，识别结束、用量与错误，不持久化响应正文。"""

from __future__ import annotations

import re
from typing import Final

from pydantic import JsonValue, TypeAdapter

_JSON: Final = TypeAdapter(dict[str, JsonValue])
_DELIMITER: Final = re.compile(rb"\r?\n\r?\n")
_MAX_EVENT: Final = 4 * 1024 * 1024


def usage_tokens(data: dict[str, JsonValue]) -> tuple[int | None, int | None]:
    response: Final = data.get("response")
    usage: Final = response.get("usage") if isinstance(response, dict) else data.get("usage")
    if not isinstance(usage, dict):
        return None, None
    input_value: Final = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_value: Final = usage.get("output_tokens", usage.get("completion_tokens"))
    return (
        input_value if type(input_value) is int and input_value >= 0 else None,
        output_value if type(output_value) is int and output_value >= 0 else None,
    )


class EventStream:
    def __init__(self) -> None:
        self.pending = b""
        self.terminal = False
        self.failed = False
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None

    def feed(self, chunk: bytes) -> tuple[bytes, ...]:
        parts: Final = _DELIMITER.split(self.pending + chunk)
        if any(len(part) > _MAX_EVENT for part in parts):
            raise ValueError("Upstream event is too large")
        self.pending = parts[-1]
        return tuple(parts[:-1])

    def observe(self, frame: bytes) -> bytes:
        payload: Final = b"\n".join(line[5:].lstrip(b" ") for line in frame.splitlines() if line.startswith(b"data:"))
        if payload == b"[DONE]":
            self.terminal = True
        elif payload:
            self.observe_payload(_JSON.validate_json(payload))
        if self.failed:
            return b'data: {"error":{"message":"Upstream stream reported an error","type":"upstream_error"}}\n\n'
        return frame + b"\n\n"

    def finish(self) -> bytes | None:
        if not self.pending:
            return None
        final: Final = self.pending
        self.pending = b""
        return final

    def observe_payload(self, event: dict[str, JsonValue]) -> None:
        input_count, output_count = usage_tokens(event)
        if input_count is not None:
            self.input_tokens = input_count
        if output_count is not None:
            self.output_tokens = output_count
        if event.get("type") in ("response.completed", "response.incomplete"):
            self.terminal = True
        if event.get("type") in ("error", "response.failed", "response.incomplete") or event.get("error") is not None:
            self.failed = True
            self.terminal = True
