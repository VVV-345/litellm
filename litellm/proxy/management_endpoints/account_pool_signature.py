"""识别签名拒绝并重建无工具副作用的完整请求，不生成签名或恢复上游隐藏状态。"""

from collections.abc import Mapping
from typing import Final

from pydantic import JsonValue, TypeAdapter

from litellm.llms.anthropic.common_utils import (
    is_anthropic_invalid_thinking_block_error,
    strip_thinking_blocks_from_anthropic_messages,
)

_HISTORY: Final = TypeAdapter(list[dict[str, JsonValue]])


def signature_error(error: JsonValue) -> bool:
    if isinstance(error, dict):
        return error.get("code") in ("thinking_signature_invalid", "invalid_encrypted_content") or any(
            signature_error(error.get(key)) for key in ("error", "message", "response")
        )
    return isinstance(error, str) and (
        "thinking_signature_invalid" in error.lower()
        or "invalid_encrypted_content" in error.lower()
        or is_anthropic_invalid_thinking_block_error(error)
    )


def text_history_item(item: JsonValue) -> bool:
    if not isinstance(item, dict) or item.get("tool_calls") or item.get("function_call"):
        return False
    if item.get("type") == "reasoning":
        return True
    if item.get("type") not in (None, "message") or item.get("role") not in (
        "user",
        "assistant",
        "system",
        "developer",
    ):
        return False
    content: Final = item.get("content")
    return (
        isinstance(content, str)
        or isinstance(content, list)
        and all(
            isinstance(block, dict)
            and block.get("type") in ("text", "input_text", "output_text", "thinking", "redacted_thinking")
            for block in content
        )
    )


def safe_signature_recovery(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue] | None:
    if any(payload.get(key) for key in ("previous_response_id", "conversation", "tools", "functions", "background")):
        return None
    history: Final = payload.get("messages", payload.get("input"))
    if not isinstance(history, list):
        return None
    if not all(text_history_item(item) for item in history):
        return None
    first_role: Final = next(
        (
            item.get("role")
            for item in history
            if isinstance(item, dict) and item.get("role") not in ("system", "developer")
        ),
        None,
    )
    if first_role != "user":
        return None
    if not any(isinstance(item, dict) and item.get("role") == "user" and item.get("content") for item in history):
        return None
    stripped: Final = _HISTORY.validate_python(strip_thinking_blocks_from_anthropic_messages(history))
    cleaned_history: Final[list[JsonValue]] = [
        {
            key: value
            for key, value in item.items()
            if key not in ("thinking_blocks", "reasoning_content", "encrypted_content")
        }
        for item in stripped
        if item.get("type") != "reasoning"
    ]
    cleaned: Final[dict[str, JsonValue]] = {
        **payload,
        "messages" if "messages" in payload else "input": cleaned_history,
    }
    return cleaned if cleaned != payload else None
