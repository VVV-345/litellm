"""声明 New API 自托管网关可稳定获取的渠道能力。"""

from typing import Final

from account_pool.domain.provider_source import (
    CapabilityState,
    ProviderCapability,
    ProviderCapabilityView,
    ProviderServiceManifest,
)

NEW_API_DEFAULT_API_BASE: Final = ""

NEW_API_MANIFEST: Final = ProviderServiceManifest(
    provider_id="new_api",
    display_name="New API 网关",
    default_api_base=NEW_API_DEFAULT_API_BASE,
    litellm_provider_prefix="openai",
    capabilities=(
        ProviderCapabilityView(
            capability=ProviderCapability.CONNECTION,
            state=CapabilityState.SUPPORTED,
            message="通过 OpenAI 兼容 GET /models 校验 URL 与 API Key",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.MODEL_DISCOVERY,
            state=CapabilityState.SUPPORTED,
            message="通过 OpenAI 兼容 GET /models 获取当前 Key 可见模型",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.MODEL_PRICING,
            state=CapabilityState.SUPPORTED,
            message="通过 GET /api/pricing 获取模型倍率与分组倍率",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.KEY_LISTING,
            state=CapabilityState.UNSUPPORTED,
            message="New API 未公开当前 Key 下的密钥管理接口",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.ACCOUNT_BALANCE,
            state=CapabilityState.UNSUPPORTED,
            message="倍率接口不返回账户余额",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.SUBSCRIPTIONS,
            state=CapabilityState.UNSUPPORTED,
            message="倍率接口不返回套餐信息",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.PERIODIC_LIMITS,
            state=CapabilityState.UNSUPPORTED,
            message="倍率接口不返回 5 小时、周或月额度窗口",
        ),
    ),
)
