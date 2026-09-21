"""本模块增量解析有界 SSE 帧，识别结束、用量与错误，不持久化响应正文。"""

from __future__ import annotations

import json
import re
from typing import Final

from pydantic import JsonValue, TypeAdapter

_JSON: Final = TypeAdapter(dict[str, JsonValue])
_DELIMITER: Final = re.compile(rb"\r?\n\r?\n")
_MAX_EVENT: Final = 4 * 1024 * 1024


def usage_tokens(data: dict[str, JsonValue]) -> tuple[int | None, int | None]:
    response: Final = data.get("response", data.get("message"))
    usage: Final = response.get("usage") if isinstance(response, dict) else data.get("usage")
    if not isinstance(usage, dict):
        return None, None
    input_value: Final = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_value: Final = usage.get("output_tokens", usage.get("completion_tokens"))
    return (
        input_value if type(input_value) is int and input_value >= 0 else None,
        output_value if type(output_value) is int and output_value >= 0 else None,
    )


def cache_usage_tokens(data: dict[str, JsonValue]) -> tuple[int | None, int | None]:
    response: Final = data.get("response", data.get("message"))
    usage: Final = response.get("usage") if isinstance(response, dict) else data.get("usage")
    if not isinstance(usage, dict):
        return None, None
    details: Final = usage.get("input_tokens_details", usage.get("prompt_tokens_details"))
    read: Final = usage.get(
        "cache_read_input_tokens", details.get("cached_tokens") if isinstance(details, dict) else None
    )
    created: Final = usage.get(
        "cache_creation_input_tokens", 0 if isinstance(details, dict) and "cached_tokens" in details else None
    )
    return (
        read if type(read) is int and read >= 0 else None,
        created if type(created) is int and created >= 0 else None,
    )


class EventStream:
    def __init__(self, *, responses_api: bool = False, anthropic_messages: bool = False) -> None:
        self.responses_api: Final = responses_api
        self.anthropic_messages: Final = anthropic_messages
        self.pending = b""
        self.terminal = False
        self.failed = False
        self.meaningful = False
        self.error_code: str | None = None
        self.error_type: str | None = None
        self.error_status = 502
        self.signature_rejected = False
        self.signature_recovery_reason: str | None = None
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.cache_read_input_tokens: int | None = None
        self.cache_creation_input_tokens: int | None = None
        self.last_sequence_number = -1

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
            error: Final = self.public_error()
            envelope: Final = self.error_envelope(error)
            return self.error_frame(envelope)
        return frame + b"\n\n"

    def error_frame(self, envelope: dict[str, JsonValue]) -> bytes:
        return (
            (b"event: error\n" if self.anthropic_messages else b"")
            + b"data: "
            + json.dumps(envelope).encode()
            + b"\n\n"
        )

    def error_envelope(self, error: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if self.anthropic_messages:
            return {"type": "error", "error": error}
        if not self.responses_api:
            return {"error": error}
        return {
            "type": "error",
            "sequence_number": self.last_sequence_number + 1,
            "message": error.get("message"),
            "code": error.get("code"),
            "param": error.get("param"),
            "error": error,
        }

    def public_error(self) -> dict[str, JsonValue]:
        from litellm.proxy.management_endpoints.account_pool_signature import signature_recovery_message

        message: Final = (
            signature_recovery_message("output_started" if self.meaningful else self.signature_recovery_reason)
            if self.signature_rejected
            else "Upstream rejected the prompt (invalid_prompt)"
            if self.error_code == "invalid_prompt"
            else "Upstream rejected the request: invalid_request_error content_policy_violation"
            if self.error_code == "content_policy_violation"
            else "Upstream stream reported an error"
        )
        return {
            "message": message,
            "type": self.error_type or ("invalid_request_error" if self.error_status == 400 else "upstream_error"),
            "code": self.error_code,
            "status_code": self.error_status,
        }

    def finish(self) -> bytes | None:
        if not self.pending:
            return None
        final: Final = self.pending
        self.pending = b""
        return final

    def observe_payload(self, event: dict[str, JsonValue]) -> None:
        sequence_number: Final = event.get("sequence_number")
        if type(sequence_number) is int:
            self.last_sequence_number = max(self.last_sequence_number, sequence_number)
        kind: Final = event.get("type")
        choices: Final = event.get("choices")
        has_output: Final = not isinstance(choices, list) or any(
            isinstance(choice, dict)
            and (
                choice.get("finish_reason")
                or isinstance(delta := choice.get("delta"), dict)
                and any(value for key, value in delta.items() if key != "role")
            )
            for choice in choices
        )
        if (
            has_output
            and event.get("error") is None
            and kind
            not in (
                "response.created",
                "response.in_progress",
                "message_start",
                "ping",
                "error",
                "response.failed",
                "response.incomplete",
            )
        ):
            self.meaningful = True
        input_count, output_count = usage_tokens(event)
        if input_count is not None:
            self.input_tokens = input_count
        if output_count is not None:
            self.output_tokens = output_count
        cache_read, cache_created = cache_usage_tokens(event)
        if cache_read is not None:
            self.cache_read_input_tokens = cache_read
        if cache_created is not None:
            self.cache_creation_input_tokens = cache_created
        if self.responses_api and event.get("type") == "response.incomplete":
            self.meaningful = True
        if event.get("type") in ("response.completed", "response.incomplete", "message_stop"):
            self.terminal = True
        if event.get("type") in ("error", "response.failed") or event.get("error") is not None:
            self.failed = True
            self.terminal = True
            response: Final = event.get("response")
            nested: Final = response.get("error") if isinstance(response, dict) else event.get("error")
            error: Final = nested if isinstance(nested, dict) else event
            from litellm.proxy.management_endpoints.account_pool_signature import signature_error

            self.signature_rejected = signature_error(error)
            code: Final = error.get("code") or error.get("type")
            self.error_code = code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code) else None
            error_type: Final = error.get("type")
            self.error_type = (
                error_type
                if isinstance(error_type, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", error_type)
                else None
            )
            self.error_status = (
                429
                if self.error_code in ("rate_limit_exceeded", "rate_limit_error", "insufficient_quota")
                else 401
                if self.error_code in ("authentication_error", "invalid_api_key")
                else 400
                if self.error_code
                in (
                    "invalid_request_error",
                    "invalid_request",
                    "context_length_exceeded",
                    "invalid_prompt",
                    "thinking_signature_invalid",
                    "invalid_encrypted_content",
                    "content_policy_violation",
                )
                or self.error_type == "invalid_request_error"
                else 503
                if self.error_code in ("server_is_overloaded", "overloaded_error", "overloaded")
                else 502
            )
