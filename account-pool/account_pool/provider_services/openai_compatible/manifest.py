from typing import Final

from account_pool.domain.provider_source import (
    CapabilityState,
    ProviderCapability,
    ProviderCapabilityView,
    ProviderServiceManifest,
)

OPENAI_COMPATIBLE_API_BASE: Final = "https://api.openai.com/v1"

OPENAI_COMPATIBLE_MANIFEST: Final = ProviderServiceManifest(
    provider_id="openai_compatible",
    display_name="OpenAI 兼容接口",
    default_api_base=OPENAI_COMPATIBLE_API_BASE,
    litellm_provider_prefix="openai",
    capabilities=(
        ProviderCapabilityView(
            capability=ProviderCapability.CONNECTION,
            state=CapabilityState.SUPPORTED,
            message="通过 OpenAI 兼容的 GET /models 校验 URL 与 API Key",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.MODEL_DISCOVERY,
            state=CapabilityState.SUPPORTED,
            message="通过 OpenAI 兼容的 GET /models 获取当前 Key 可见模型",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.KEY_LISTING,
            state=CapabilityState.UNSUPPORTED,
            message="OpenAI 兼容协议没有统一的 Key 管理接口",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.ACCOUNT_BALANCE,
            state=CapabilityState.UNSUPPORTED,
            message="OpenAI 兼容协议没有统一的账户余额接口",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.SUBSCRIPTIONS,
            state=CapabilityState.UNSUPPORTED,
            message="OpenAI 兼容协议没有统一的套餐接口",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.PERIODIC_LIMITS,
            state=CapabilityState.UNSUPPORTED,
            message="OpenAI 兼容协议没有统一的周期额度接口",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.MODEL_PRICING,
            state=CapabilityState.UNSUPPORTED,
            message="模型列表不包含账户实际价格、分组倍率或资源包抵扣信息",
        ),
    ),
)
