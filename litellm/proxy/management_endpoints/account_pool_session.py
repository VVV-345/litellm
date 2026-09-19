"""统一号池路由和日志的会话识别，只使用客户端提供的稳定标识。"""

from collections.abc import Mapping
from typing import Final, cast

from litellm.exceptions import ServiceUnavailableError
from litellm.proxy.litellm_pre_call_utils import (
    _get_anthropic_session_id_from_metadata,  # pyright: ignore[reportPrivateUsage]  # Reuse the proxy's Claude session parser to keep identity rules consistent.
)

SESSION_HEADERS: Final = (
    "x-litellm-session-id",
    "x-claude-code-session-id",
    "x-session-id",
    "session-id",
    "session_id",
    "thread-id",
    "thread_id",
    "conversation_id",
    "x-session-affinity",
)


class AccountPoolSessionUnavailableError(ServiceUnavailableError):
    pass


def normalized_session(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        return None
    return None if any(ord(char) < 32 or ord(char) == 127 for char in value) else value.strip()


def session_identifier(headers: Mapping[str, str], payload: Mapping[str, object]) -> str | None:
    header: Final = next((value for name in SESSION_HEADERS if (value := normalized_session(headers.get(name)))), None)
    if header is not None:
        return header
    metadata: Final = payload.get("metadata")
    native: Final = payload.get("litellm_metadata")
    sources: Final = tuple(
        cast(Mapping[str, object], value) for value in (native, metadata) if isinstance(value, Mapping)
    )
    return next(
        (
            session
            for value in (
                *(source.get("session_id") for source in sources),
                _get_anthropic_session_id_from_metadata(metadata),
                payload.get("session_id"),
                payload.get("conversation_id"),
                payload.get("thread_id"),
                payload.get("prompt_cache_key"),
            )
            if (session := normalized_session(value)) is not None
        ),
        None,
    )


def has_signed_history(value: object) -> bool:
    if isinstance(value, Mapping):
        record: Final = cast(Mapping[str, object], value)
        return bool(
            record.get("previous_response_id")
            or record.get("encrypted_content")
            or record.get("signature")
            or record.get("thought_signature")
            or record.get("thoughtSignature")
            or record.get("type") in ("thinking", "redacted_thinking")
        ) or any(has_signed_history(record.get(key)) for key in ("messages", "input", "content", "thinking_blocks"))
    return isinstance(value, list) and any(has_signed_history(item) for item in cast(list[object], value))
