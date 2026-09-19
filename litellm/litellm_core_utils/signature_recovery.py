"""校验可重放的客户端工具历史，复用原生思考块清理，不重放服务端工具。"""

from collections.abc import Mapping
from typing import Final

from pydantic import JsonValue, TypeAdapter

from litellm.llms.anthropic.common_utils import strip_thinking_blocks_from_anthropic_messages

_HISTORY: Final = TypeAdapter(list[dict[str, JsonValue]])
_CALLS: Final = frozenset(("function_call", "custom_tool_call", "tool_use"))
_RESULTS: Final = frozenset(("function_call_output", "custom_tool_call_output", "tool_result"))
_CONTENT: Final = frozenset(
    (
        "text",
        "input_text",
        "output_text",
        "image",
        "input_image",
        "image_url",
        "document",
        "input_file",
        "thinking",
        "redacted_thinking",
    )
)


def _client_tool(tool: JsonValue) -> bool:
    if not isinstance(tool, dict):
        return False
    kind: Final = tool.get("type")
    if kind not in (None, "function", "custom"):
        return False
    definition: Final = tool.get("function", tool)
    return isinstance(definition, dict) and isinstance(definition.get("name"), str) and bool(definition.get("name"))


def _tool_events(item: Mapping[str, JsonValue]) -> tuple[tuple[str, str], ...] | None:
    kind: Final = item.get("type")
    if isinstance(kind, str) and kind in _CALLS | _RESULTS:
        identifier: Final = (
            item.get("tool_use_id")
            if kind == "tool_result"
            else item.get("id")
            if kind == "tool_use"
            else item.get("call_id")
        )
        return (
            (("call" if kind in _CALLS else "result", identifier),)
            if isinstance(identifier, str) and identifier
            else None
        )
    if kind == "reasoning":
        return ()
    if kind not in (None, "message") or item.get("function_call"):
        return None
    role: Final = item.get("role")
    if role == "tool":
        result_id: Final = item.get("tool_call_id")
        return (("result", result_id),) if isinstance(result_id, str) and result_id else None
    if role not in ("user", "assistant", "system", "developer"):
        return None
    calls: Final = item.get("tool_calls", [])
    if not isinstance(calls, list) or any(
        not isinstance(call, dict)
        or call.get("type") != "function"
        or not isinstance(call.get("id"), str)
        or not call.get("id")
        for call in calls
    ):
        return None
    if calls and role != "assistant":
        return None
    content: Final = item.get("content")
    if isinstance(content, str) or content is None and calls:
        return tuple(("call", str(call["id"])) for call in calls if isinstance(call, dict))
    if not isinstance(content, list):
        return None
    blocks: Final = tuple(
        ()
        if isinstance(block, dict) and isinstance(block.get("type"), str) and block.get("type") in _CONTENT
        else _tool_events(block)
        if isinstance(block, dict)
        and (
            block.get("type") == "tool_use"
            and role == "assistant"
            or block.get("type") == "tool_result"
            and role == "user"
        )
        else None
        for block in content
    )
    if any(events is None for events in blocks):
        return None
    return (
        *tuple(("call", str(call["id"])) for call in calls if isinstance(call, dict)),
        *tuple(event for events in blocks if events is not None for event in events),
    )


def signature_recovery_reason(payload: Mapping[str, JsonValue]) -> str | None:
    if any(payload.get(key) for key in ("previous_response_id", "conversation", "background")):
        return "server_state_required"
    if payload.get("functions"):
        return "legacy_function_history"
    tools: Final = payload.get("tools", [])
    if not isinstance(tools, list) or not all(_client_tool(tool) for tool in tools):
        return "server_or_unknown_tools"
    history: Final = payload.get("messages", payload.get("input"))
    if not isinstance(history, list) or not history:
        return "incomplete_history"
    first: Final = next(
        (
            item
            for item in history
            if isinstance(item, dict)
            and item.get("type") != "reasoning"
            and item.get("role") not in ("system", "developer")
        ),
        None,
    )
    if first is None or first.get("role") != "user" or not first.get("content"):
        return "incomplete_history"
    inspected: Final = tuple(_tool_events(item) if isinstance(item, dict) else None for item in history)
    if any(events is None for events in inspected):
        return "unsupported_history"
    events: Final = tuple(event for group in inspected if group is not None for event in group)
    calls: Final = tuple(identifier for kind, identifier in events if kind == "call")
    results: Final = tuple(identifier for kind, identifier in events if kind == "result")
    if len(set(calls)) != len(calls) or len(set(results)) != len(results) or set(calls) != set(results):
        return "unpaired_tool_history"
    positions: Final = {identifier: index for index, (kind, identifier) in enumerate(events) if kind == "call"}
    if any(positions[identifier] >= index for index, (kind, identifier) in enumerate(events) if kind == "result"):
        return "out_of_order_tool_history"
    return None


def recover_signature_history(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue] | None:
    if signature_recovery_reason(payload) is not None:
        return None
    history: Final = payload.get("messages", payload.get("input"))
    stripped: Final = _HISTORY.validate_python(
        strip_thinking_blocks_from_anthropic_messages(_HISTORY.validate_python(history))
    )
    cleaned_history: Final[list[JsonValue]] = [
        {
            key: value
            for key, value in item.items()
            if key not in ("thinking_blocks", "reasoning_content", "encrypted_content")
        }
        for item in stripped
        if item.get("type") != "reasoning"
    ]
    if cleaned_history == history:
        return None
    cleaned: Final[dict[str, JsonValue]] = {
        **{key: value for key, value in payload.items() if key != "thinking"},
        "messages" if "messages" in payload else "input": cleaned_history,
    }
    return cleaned if cleaned != payload else None
