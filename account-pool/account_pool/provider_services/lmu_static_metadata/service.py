"""将 LMU 公开静态模型元数据转换为统一渠道校验结果。"""

from __future__ import annotations

from account_pool.domain.provider_source import (
    ModelOffer,
    ProviderServiceManifest,
    ProviderValidationRequest,
    ProviderValidationResult,
)
from account_pool.provider_services.lmu_static_metadata.client import (
    HostResolver,
    HttpClientFactory,
    LmuStaticMetadataFailure,
    fetch_lmu_static_metadata,
    resolve_host_addresses,
)
from account_pool.provider_services.lmu_static_metadata.manifest import LMU_STATIC_METADATA_MANIFEST


class LmuStaticMetadataProviderService:
    def __init__(
        self,
        resolve_host: HostResolver = resolve_host_addresses,
        client_factory: HttpClientFactory | None = None,
    ) -> None:
        self._resolve_host = resolve_host
        self._client_factory = client_factory

    @property
    def manifest(self) -> ProviderServiceManifest:
        return LMU_STATIC_METADATA_MANIFEST

    async def validate(self, request: ProviderValidationRequest) -> ProviderValidationResult:
        fetched = await fetch_lmu_static_metadata(
            api_base=request.api_base,
            resolve_host=self._resolve_host,
            client_factory=self._client_factory,
        )
        if isinstance(fetched, LmuStaticMetadataFailure):
            return ProviderValidationResult(
                ok=False,
                provider_id=self.manifest.provider_id,
                normalized_api_base=fetched.api_base,
                group=request.group,
                key_fingerprint=None,
                message=fetched.message,
                failure_code=fetched.code,
                capabilities=self.manifest.capabilities,
            )
        return ProviderValidationResult(
            ok=True,
            provider_id=self.manifest.provider_id,
            normalized_api_base=fetched.api_base,
            group=request.group,
            key_fingerprint=None,
            message=f"公开静态页面发现 {len(fetched.models)} 个模型，不代表当前账户可见模型或实际价格",
            capabilities=self.manifest.capabilities,
            models=tuple(ModelOffer(model=model, pricing_source="public_static_metadata") for model in fetched.models),
        )
