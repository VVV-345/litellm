"""识别签名拒绝并重建无工具副作用的完整请求，不生成签名或恢复上游隐藏状态。"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, Field, JsonValue, TypeAdapter

from litellm.litellm_core_utils.signature_recovery import recover_signature_history
from litellm.llms.anthropic.common_utils import (
    is_anthropic_invalid_thinking_block_error,
)
from litellm.router_utils.pre_call_checks.encrypted_content_affinity_check import EncryptedContentAffinityCheck

if TYPE_CHECKING:
    from litellm import Router


class _PoolModelInfo(BaseModel):
    account_pool_environment_id: str | None = None


class _PoolDeployment(BaseModel):
    model_info: _PoolModelInfo | None = Field(default_factory=_PoolModelInfo)


_DEPLOYMENTS: Final = TypeAdapter(tuple[_PoolDeployment, ...])
_PAYLOAD: Final = TypeAdapter(dict[str, JsonValue])
_HISTORY_FIELDS: Final = frozenset(
    ("input", "messages", "tools", "functions", "previous_response_id", "conversation", "background", "thinking")
)


def foreign_history_recovery(payload: Mapping[str, object], router: "Router") -> dict[str, JsonValue]:
    from litellm._logging import verbose_proxy_logger
    from litellm.proxy.management_endpoints.account_pool_integration import pool_identity

    identity: Final = pool_identity.get()
    model: Final = payload.get("model")
    if identity is None or not isinstance(model, str):
        return {}
    origin: Final = EncryptedContentAffinityCheck._extract_model_id_from_input(payload.get("input"))  # pyright: ignore[reportPrivateUsage]  # Reuse the native marker decoder.
    if origin is None or router.get_deployment(model_id=origin) is not None:
        return {}
    candidates: Final = _DEPLOYMENTS.validate_python(router.get_model_list(model_name=model) or [])
    if not any(item.model_info is not None and item.model_info.account_pool_environment_id for item in candidates):
        return {}
    history: Final = _PAYLOAD.validate_python({key: value for key, value in payload.items() if key in _HISTORY_FIELDS})
    recovered: Final = recover_signature_history(history)
    if recovered is None:
        return {}
    verbose_proxy_logger.info("Account pool signature recovery: foreign origin; request_id=%s", identity.request_id)
    return {key: value for key, value in recovered.items() if key in ("input", "messages")}


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


def safe_signature_recovery(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue] | None:
    return recover_signature_history(payload)


def signature_recovery_message(reason: str | None) -> str:
    category: Final = (reason or "no_removable_state").split(":", 1)[0]
    explanation: Final = {
        "encrypted_compaction": "会话包含加密的压缩上下文，当前上游无法读取，不能直接删除而丢失上下文",
        "server_item_reference": "会话引用了原上游保存的消息，当前上游无法读取这些消息",
        "server_state_required": "请求依赖原上游保存的会话状态，无法在当前上游重建",
        "incomplete_history": "客户端没有携带可用于重建会话的完整历史",
        "unpaired_tool_history": "工具调用和结果不完整，无法安全重试",
        "out_of_order_tool_history": "工具调用和结果顺序不正确，无法安全重试",
        "server_or_unknown_tools": "请求包含服务端工具或尚未支持的工具，无法安全重试",
        "unsupported_content": "会话包含尚未支持的历史内容格式",
        "unsupported_history": "会话包含尚未支持的消息或工具格式",
        "retry_exhausted": "已在同一卡片清理旧思考状态并重试一次，上游仍拒绝",
        "output_started": "本次响应已开始输出，为避免重复执行已停止重试",
    }.get(category, "原会话的思考状态无法由当前上游验证")
    return f"跨上游思考状态不兼容：{explanation}。请新建会话，或回到原中转站继续原会话。恢复原因：{reason or 'no_removable_state'}"
