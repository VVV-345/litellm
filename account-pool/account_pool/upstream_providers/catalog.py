"""声明上游厂商的模型列表请求方式。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from account_pool.upstream_providers.models import UpstreamProviderManifest


class ModelListingProtocol(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    OLLAMA = "ollama"


@dataclass(frozen=True, slots=True)
class UpstreamProviderDefinition:
    manifest: UpstreamProviderManifest
    protocol: ModelListingProtocol


def _definition(
    provider_id: str,
    display_name: str,
    default_api_base: str,
    protocol: ModelListingProtocol,
) -> UpstreamProviderDefinition:
    return UpstreamProviderDefinition(
        manifest=UpstreamProviderManifest(
            provider_id=provider_id,
            display_name=display_name,
            default_api_base=default_api_base,
        ),
        protocol=protocol,
    )


UPSTREAM_PROVIDER_DEFINITIONS: Final[tuple[UpstreamProviderDefinition, ...]] = (
    _definition(
        "openai",
        "OpenAI",
        "https://api.openai.com/v1",
        ModelListingProtocol.OPENAI_COMPATIBLE,
    ),
    _definition(
        "anthropic",
        "Anthropic",
        "https://api.anthropic.com/v1",
        ModelListingProtocol.ANTHROPIC,
    ),
    _definition(
        "gemini",
        "Google Gemini",
        "https://generativelanguage.googleapis.com/v1beta",
        ModelListingProtocol.GEMINI,
    ),
    _definition(
        "zhipu",
        "智谱 GLM",
        "https://open.bigmodel.cn/api/paas/v4",
        ModelListingProtocol.OPENAI_COMPATIBLE,
    ),
    _definition(
        "dashscope",
        "阿里云百炼 DashScope",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ModelListingProtocol.OPENAI_COMPATIBLE,
    ),
    _definition(
        "deepseek",
        "DeepSeek",
        "https://api.deepseek.com",
        ModelListingProtocol.OPENAI_COMPATIBLE,
    ),
    _definition(
        "moonshot",
        "Moonshot",
        "https://api.moonshot.cn/v1",
        ModelListingProtocol.OPENAI_COMPATIBLE,
    ),
    _definition(
        "openrouter",
        "OpenRouter",
        "https://openrouter.ai/api/v1",
        ModelListingProtocol.OPENAI_COMPATIBLE,
    ),
    _definition(
        "siliconflow",
        "SiliconFlow",
        "https://api.siliconflow.cn/v1",
        ModelListingProtocol.OPENAI_COMPATIBLE,
    ),
    _definition(
        "volcengine",
        "火山方舟 Volcengine Ark",
        "https://ark.cn-beijing.volces.com/api/v3",
        ModelListingProtocol.OPENAI_COMPATIBLE,
    ),
    _definition(
        "new_api",
        "New API 网关",
        "",
        ModelListingProtocol.OPENAI_COMPATIBLE,
    ),
    _definition(
        "openai_compatible",
        "OpenAI 兼容接口",
        "",
        ModelListingProtocol.OPENAI_COMPATIBLE,
    ),
    _definition(
        "glm_official",
        "智谱 GLM（旧渠道兼容）",
        "https://open.bigmodel.cn/api/paas/v4",
        ModelListingProtocol.OPENAI_COMPATIBLE,
    ),
)
