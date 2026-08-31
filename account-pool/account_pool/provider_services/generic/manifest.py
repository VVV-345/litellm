"""声明通用解析器可稳定处理的兼容接口。"""

from typing import Final

from account_pool.domain.provider_source import (
    CapabilityState,
    ProviderCapability,
    ProviderCapabilityView,
    ProviderServiceManifest,
)

GENERIC_MANIFEST: Final = ProviderServiceManifest(
    provider_id="generic",
    display_name="通用解析器",
    default_api_base="",
    litellm_provider_prefix="openai",
    capabilities=(
        ProviderCapabilityView(
            capability=ProviderCapability.CONNECTION,
            state=CapabilityState.SUPPORTED,
            message="通过兼容 GET /models 校验 URL 与 API Key",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.MODEL_DISCOVERY,
            state=CapabilityState.SUPPORTED,
            message="通过兼容 GET /models 获取当前 Key 可见模型",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.MODEL_PRICING,
            state=CapabilityState.SUPPORTED,
            message="尝试读取兼容 GET /api/pricing 的模型价格；失败后保留人工补充入口",
        ),
    ),
)
