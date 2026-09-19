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
