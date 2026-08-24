"""声明 LMU 公开静态模型页面可安全获取的能力边界。"""

from typing import Final

from account_pool.domain.provider_source import (
    CapabilityState,
    ProviderCapability,
    ProviderCapabilityView,
    ProviderServiceManifest,
)

LMU_STATIC_METADATA_ORIGIN: Final = "https://api.lmuai.com"
LMU_STATIC_METADATA_PATH: Final = "/models"

LMU_STATIC_METADATA_MANIFEST: Final = ProviderServiceManifest(
    provider_id="lmu_static_metadata",
    display_name="LMU 公开静态元数据",
    default_api_base=LMU_STATIC_METADATA_ORIGIN,
    litellm_provider_prefix="openai",
    capabilities=(
        ProviderCapabilityView(
            capability=ProviderCapability.CONNECTION,
            state=CapabilityState.SUPPORTED,
            message="仅获取固定公开静态页面，不携带渠道凭证",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.MODEL_DISCOVERY,
            state=CapabilityState.SUPPORTED,
            message="仅接受结构化 JSON-LD 模型清单，不执行页面脚本",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.MODEL_PRICING,
            state=CapabilityState.UNAVAILABLE,
            message="公开页面不能证明账户实际倍率或价格，需要人工补充",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.KEY_LISTING,
            state=CapabilityState.UNSUPPORTED,
            message="公开静态页面不读取密钥信息",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.ACCOUNT_BALANCE,
            state=CapabilityState.UNSUPPORTED,
            message="公开静态页面不读取账户余额",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.SUBSCRIPTIONS,
            state=CapabilityState.UNSUPPORTED,
            message="公开静态页面不读取账户套餐",
        ),
        ProviderCapabilityView(
            capability=ProviderCapability.PERIODIC_LIMITS,
            state=CapabilityState.UNSUPPORTED,
            message="公开静态页面不读取账户额度窗口",
        ),
    ),
)
