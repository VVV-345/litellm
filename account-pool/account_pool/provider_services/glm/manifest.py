"""声明 GLM 官方开放平台当前可稳定调用的公开能力。"""

from typing import Final

from account_pool.domain.provider_source import (
    CapabilityState,
    ProviderCapability,
    ProviderCapabilityView,
    ProviderServiceManifest,
)

GLM_OFFICIAL_API_BASE: Final = "https://open.bigmodel.cn/api/paas/v4"

GLM_OFFICIAL_MANIFEST: Final = ProviderServiceManifest(
    provider_id="glm_official",
    display_name="智谱 GLM 官方开放平台",
    default_api_base=GLM_OFFICIAL_API_BASE,
    litellm_provider_prefix="zai",
    capabilities=(
        ProviderCapabilityView(
            capability=ProviderCapability.CONNECTION,
            state=CapabilityState.SUPPORTED,
            message="通过官方模型列表接口校验 URL 与 API Key",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.MODEL_DISCOVERY,
            state=CapabilityState.SUPPORTED,
            message="通过 GET /models 获取该 Key 可见模型",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.KEY_LISTING,
            state=CapabilityState.UNSUPPORTED,
            message="官方推理 API 未公开账户下的 Key 管理接口",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.ACCOUNT_BALANCE,
            state=CapabilityState.UNSUPPORTED,
            message="官方推理 API 未公开账户余额接口",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.SUBSCRIPTIONS,
            state=CapabilityState.UNSUPPORTED,
            message="套餐信息目前只能在官方控制台查看",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.PERIODIC_LIMITS,
            state=CapabilityState.UNSUPPORTED,
            message="5 小时、周限和月限目前没有公开查询接口",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.MODEL_PRICING,
            state=CapabilityState.UNSUPPORTED,
            message="模型列表接口不返回账户折扣、资源包抵扣或实际成交价",
        ),
    ),
)
