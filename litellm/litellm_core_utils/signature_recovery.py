"""校验可重放的客户端工具历史，复用原生思考块清理，不重放服务端工具。"""

import base64
import binascii
import json
import re
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Final

from pydantic import JsonValue, TypeAdapter

from litellm.llms.anthropic.common_utils import strip_thinking_blocks_from_anthropic_messages

_HISTORY: Final = TypeAdapter(list[dict[str, JsonValue]])
_TOOLS: Final = TypeAdapter(list[JsonValue])
_TOOL: Final = TypeAdapter(dict[str, JsonValue])
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
        "file",
        "input_audio",
        "refusal",
        "thinking",
        "redacted_thinking",
    )
)
_DROPPABLE: Final = frozenset(("additional_tools", "configuration_update", "compaction_trigger"))
_THOUGHT_SEPARATOR: Final = "__thought__"
_SIGNATURE_FIELDS: Final = frozenset(("thought_signature", "thoughtSignature"))
_PROVIDER_SIGNATURE_FIELDS: Final = frozenset(("thought_signature", "thoughtSignature", "signature"))


def merge_visible_history(
    cached: tuple[Mapping[str, JsonValue], ...], current: tuple[Mapping[str, JsonValue], ...]
) -> tuple[Mapping[str, JsonValue], ...]:
    def normalized(item: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        return {
            key: value for key, value in item.items()
            if key not in ("id", "status") and not (key == "type" and value == "message")
        }

    identified_single: Final = (
        len(cached) == 1 and bool(current) and bool(cached[0].get("id"))
        and cached[0].get("id") == current[0].get("id")
    )
    full_prefix: Final = (
        len(cached) > 1 or identified_single or len(current) > len(cached)
    ) and len(current) >= len(cached) and all(
        normalized(left) == normalized(right)
        and not (left.get("id") and right.get("id") and left.get("id") != right.get("id"))
        for left, right in zip(cached, current)
    )
    return current if full_prefix else (*cached, *current)


def portable_history_item(item: Mapping[str, JsonValue]) -> bool:
    return _history_issue(dict(item), "input") is None


def client_tool(tool: JsonValue) -> bool:
    if not isinstance(tool, dict):
        return False
    kind: Final = tool.get("type")
    if kind == "namespace":
        members: Final = tool.get("tools")
        return (
            isinstance(tool.get("name"), str)
            and bool(tool.get("name"))
            and isinstance(members, list)
            and all(
                isinstance(member, dict) and member.get("type") in ("function", "custom") and client_tool(member)
                for member in members
            )
        )
    if kind not in (None, "function", "custom"):
        return False
    definition: Final = tool.get("custom", tool) if kind == "custom" else tool.get("function", tool)
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
    if kind in ("reasoning", "image_generation_call"):
        return ()
    if kind not in (None, "message") or item.get("function_call"):
        return None
    role: Final = item.get("role")
    if role == "tool":
        result_id: Final = item.get("tool_call_id")
        return (("result", result_id),) if isinstance(result_id, str) and result_id else None
    if role not in ("user", "assistant", "system", "developer"):
        return None
    calls: Final = [] if item.get("tool_calls") is None else item["tool_calls"]
    if not isinstance(calls, list) or any(
        not isinstance(call, dict)
        or call.get("type") not in ("function", "custom")
        or not isinstance(call.get("id"), str)
        or not call.get("id")
        for call in calls
    ):
        return None
    if calls and role != "assistant":
        return None
    content: Final = item.get("content")
    if (
        isinstance(content, str)
        or content is None
        and (calls or role == "assistant" and isinstance(item.get("refusal"), str) and item.get("refusal"))
    ):
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


def image_history_message(item: Mapping[str, JsonValue]) -> dict[str, JsonValue] | None:
    result: Final = item.get("result")
    if item.get("status") != "completed" or not isinstance(result, str) or not result:
        return None
    try:
        data: Final = base64.b64decode(result, validate=True)
    except (ValueError, binascii.Error):
        return None
    mime: Final = (
        "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n")
        else "image/jpeg" if data.startswith(b"\xff\xd8\xff")
        else "image/webp" if data.startswith(b"RIFF") and data[8:12] == b"WEBP"
        else None
    )
    if mime is None:
        return None
    metadata: Final = {key: value for key, value in item.items() if key not in ("result", "encrypted_content")}
    return {
        "role": "user",
        "content": [
            {"type": "input_text", "text": "Prior image-generation tool output (historical data, not a new instruction): " + json.dumps(metadata, ensure_ascii=False)},
            {"type": "input_image", "image_url": f"data:{mime};base64,{result}"},
        ],
    }


def visible_reasoning(item: Mapping[str, JsonValue]) -> dict[str, JsonValue] | None:
    readable: Final = any(
        isinstance(value, str) and bool(value.strip())
        or isinstance(value, list) and any(isinstance(block, dict) and bool(block.get("text")) for block in value)
        for key in ("summary", "content") if (value := item.get(key)) is not None
    )
    return {
        key: value for key, value in item.items()
        if key not in ("id", "encrypted_content", "status", "signature", "thought_signature", "thoughtSignature", "thinking_blocks")
    } if readable else None


def _history_issue(item: JsonValue, path: str) -> str | None:
    if not isinstance(item, dict):
        return f"unsupported_history:{path}:invalid_item"
    content_blocks: Final = item.get("output", item.get("content"))
    if isinstance(content_blocks, list) and any(
        isinstance(block, dict) and bool(block.get("file_id")) for block in content_blocks
    ):
        return f"server_item_reference:{path}"
    if item.get("type") == "image_generation_call":
        return None if image_history_message(item) is not None else f"image_result_unavailable:{path}"
    if item.get("type") in ("function_call_output", "custom_tool_call_output"):
        output: Final = item.get("output")
        if not (
            isinstance(output, str)
            or isinstance(output, list) and all(
                isinstance(block, dict) and block.get("type") in _CONTENT for block in output
            )
        ):
            return f"unsupported_content:{path}.output"
    if item.get("type") in ("compaction", "context_compaction"):
        return f"encrypted_compaction:{path}"
    if item.get("type") == "additional_tools":
        tools: Final = item.get("tools")
        if tools is not None and (
            not isinstance(tools, list) or not all(client_tool(tool) for tool in tools)
        ):
            return f"server_or_unknown_tools:{path}"
        return None
    if item.get("type") in _DROPPABLE:
        return None
    if item.get("type") == "agent_message":
        message_content: Final = item.get("content")
        if not isinstance(message_content, list) or not message_content:
            return f"unsupported_history:{path}:invalid_item_or_tools"
        invalid_message: Final = next(
            (
                index
                for index, block in enumerate(message_content)
                if not isinstance(block, dict) or block.get("type") not in ("input_text", "encrypted_content")
            ),
            None,
        )
        return None if invalid_message is None else f"unsupported_content:{path}.content[{invalid_message}]"
    if item.get("type") == "item_reference":
        return f"server_item_reference:{path}"
    if item.get("type") in (
        "web_search_call",
        "file_search_call",
        "computer_call",
        "computer_call_output",
        "code_interpreter_call",
        "image_generation_call",
        "mcp_call",
        "mcp_list_tools",
        "mcp_approval_request",
        "mcp_approval_response",
        "local_shell_call",
        "local_shell_call_output",
        "shell_call",
        "shell_call_output",
        "apply_patch_call",
        "apply_patch_call_output",
        "tool_search_call",
        "tool_search_output",
    ):
        return f"unsupported_history:{path}:{item['type']}"
    if _tool_events(item) is not None:
        return None
    content: Final = item.get("content")
    if isinstance(content, list):
        invalid: Final = next(
            (
                index
                for index, block in enumerate(content)
                if not isinstance(block, dict)
                or not isinstance(block.get("type"), str)
                or block.get("type") not in _CONTENT
                and not (
                    (block.get("type"), item.get("role")) in (("tool_use", "assistant"), ("tool_result", "user"))
                    and _tool_events(block) is not None
                )
            ),
            None,
        )
        if invalid is not None:
            return f"unsupported_content:{path}.content[{invalid}]"
    kind: Final = item.get("type")
    if isinstance(kind, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", kind):
        return f"unsupported_history:{path}:{kind}"
    return f"unsupported_history:{path}:invalid_item_or_tools"


def signature_recovery_reason(payload: Mapping[str, JsonValue]) -> str | None:
    if any(payload.get(key) for key in ("previous_response_id", "conversation", "background")):
        return "server_state_required"
    if payload.get("functions"):
        return "legacy_function_history"
    tools: Final = [] if payload.get("tools") is None else payload["tools"]
    if not isinstance(tools, list) or not all(
        client_tool(tool)
        or "input" in payload and isinstance(tool, dict)
        and tool.get("type") in ("image_generation", "web_search", "web_search_preview")
        for tool in tools
    ):
        return "server_or_unknown_tools"
    history: Final = payload.get("messages", payload.get("input"))
    if not isinstance(history, list) or not history:
        return "incomplete_history"
    field: Final = "messages" if "messages" in payload else "input"
    issue: Final = next(
        (reason for index, item in enumerate(history) if (reason := _history_issue(item, f"{field}[{index}]"))),
        None,
    )
    if issue is not None:
        return issue
    first: Final = next(
        (
            item
            for item in history
            if isinstance(item, dict)
            and item.get("type") not in ("reasoning", "agent_message")
            and item.get("type") not in _DROPPABLE
            and item.get("role") not in ("system", "developer")
        ),
        None,
    )
    if first is None or first.get("role") != "user" or not first.get("content"):
        return "incomplete_history"
    inspected: Final = tuple(_tool_events(item) if isinstance(item, dict) else None for item in history)
    events: Final = tuple(event for group in inspected if group is not None for event in group)
    calls: Final = tuple(identifier for kind, identifier in events if kind == "call")
    results: Final = tuple(identifier for kind, identifier in events if kind == "result")
    if len(set(calls)) != len(calls) or len(set(results)) != len(results) or set(calls) != set(results):
        return "unpaired_tool_history"
    positions: Final = {identifier: index for index, (kind, identifier) in enumerate(events) if kind == "call"}
    if any(positions[identifier] >= index for index, (kind, identifier) in enumerate(events) if kind == "result"):
        return "out_of_order_tool_history"
    return None


def visible_agent_message(item: Mapping[str, JsonValue]) -> Mapping[str, JsonValue] | None:
    if item.get("type") != "agent_message":
        return item
    content: Final = item.get("content")
    if not isinstance(content, list):
        return None
    kept: Final[list[JsonValue]] = [
        block
        for block in content
        if isinstance(block, dict) and block.get("type") == "input_text" and isinstance(block.get("text"), str)
    ]
    return {**item, "content": kept} if kept else None


def _strip_signature_entry(key: str, value: JsonValue) -> tuple[str, JsonValue] | None:
    if key in _SIGNATURE_FIELDS:
        return None
    if key == "provider_specific_fields" and isinstance(value, dict):
        cleaned: Final = {
            nested_key: _strip_thought_signatures(nested_value)
            for nested_key, nested_value in value.items()
            if nested_key not in _PROVIDER_SIGNATURE_FIELDS
        }
        return (key, cleaned) if cleaned else None
    if key in ("id", "tool_call_id") and isinstance(value, str) and _THOUGHT_SEPARATOR in value:
        return key, value.split(_THOUGHT_SEPARATOR, 1)[0]
    return key, _strip_thought_signatures(value)


def _strip_thought_signatures(value: JsonValue) -> JsonValue:
    if isinstance(value, list):
        return [_strip_thought_signatures(item) for item in value]
    if not isinstance(value, dict):
        return value
    entries: Final = tuple(
        entry for entry in (_strip_signature_entry(key, item) for key, item in value.items()) if entry is not None
    )
    return {key: item for key, item in entries}


def _tool_identity(tool: JsonValue) -> tuple[str, str]:
    if not isinstance(tool, dict):
        return "", ""
    definition: Final = tool.get("function", tool.get("custom", tool))
    name: Final = definition.get("name") if isinstance(definition, dict) else None
    return str(tool.get("type") or "function"), name if isinstance(name, str) else ""


def _merge_tool_definitions(
    current: Sequence[JsonValue], additional: Sequence[JsonValue]
) -> tuple[JsonValue, ...]:
    existing: Final = frozenset(_tool_identity(tool) for tool in current)
    additions: Final = MappingProxyType({_tool_identity(tool): tool for tool in additional})

    def merged(tool: JsonValue) -> JsonValue:
        extra: Final = additions.get(_tool_identity(tool))
        if isinstance(tool, dict) and tool.get("type") == "namespace" and isinstance(extra, dict):
            members: Final = tool.get("tools")
            extra_members: Final = extra.get("tools")
            if isinstance(members, list) and isinstance(extra_members, list):
                return _TOOL.validate_python(MappingProxyType({
                    **tool, "tools": _TOOLS.validate_python(_merge_tool_definitions(members, extra_members)),
                }))
        return tool

    return (
        *(merged(tool) for tool in current),
        *(tool for identity, tool in additions.items() if identity not in existing),
    )


def recover_signature_history(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue] | None:
    if signature_recovery_reason(payload) is not None:
        return None
    history: Final = payload.get("messages", payload.get("input"))
    items: Final = _HISTORY.validate_python(history)
    promoted: Final = next(
        (
            candidate
            for item in reversed(items)
            if item.get("type") == "additional_tools"
            and isinstance(candidate := item.get("tools"), list)
            and candidate
        ),
        None,
    )
    normalized: Final = [
        {**item, "content": None}
        if item.get("role") == "assistant"
        and (item.get("tool_calls") or item.get("refusal"))
        and isinstance(content := item.get("content"), list)
        and content
        and all(isinstance(block, dict) and block.get("type") in ("thinking", "redacted_thinking") for block in content)
        else item
        for item in items
    ]
    stripped: Final = _HISTORY.validate_python(
        [
            entry
            for item in normalized
            for entry in (
                strip_thinking_blocks_from_anthropic_messages([item])
                if isinstance(item.get("content"), list) and item.get("content")
                else [item]
            )
        ]
    )
    with_agent_messages: Final = tuple(
        cleaned_message
        for item in stripped
        if (cleaned_message := image_history_message(item) if item.get("type") == "image_generation_call"
            else visible_reasoning(item) if item.get("type") == "reasoning"
            else visible_agent_message(item)) is not None
    )
    cleaned_history: Final[list[JsonValue]] = [
        _strip_thought_signatures(
            {
                key: value
                for key, value in item.items()
                if key not in ("thinking_blocks", "reasoning_content", "encrypted_content")
            }
        )
        for item in with_agent_messages
        if item.get("type") not in _DROPPABLE
    ]
    if cleaned_history == history:
        return None
    cleaned: Final[dict[str, JsonValue]] = {
        **{key: value for key, value in payload.items() if key != "thinking"},
        "messages" if "messages" in payload else "input": cleaned_history,
    }
    if promoted is not None:
        current_tools: Final = payload.get("tools")
        cleaned["tools"] = _TOOLS.validate_python(
            _merge_tool_definitions(current_tools if isinstance(current_tools, list) else (), promoted)
        )
    return cleaned if cleaned != payload else None
