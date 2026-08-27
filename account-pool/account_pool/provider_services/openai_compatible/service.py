"""将 OpenAI 兼容模型发现结果转换为统一渠道校验结果。"""

from __future__ import annotations

from hashlib import sha256
from typing import Final

import httpx

from account_pool.domain.provider_source import (
    ModelOffer,
    ProviderServiceManifest,
    ProviderValidationFailureCode,
    ProviderValidationRequest,
    ProviderValidationResult,
)
from account_pool.provider_services.openai_compatible.client import (
    HostResolver,
    HttpClientFactory,
    OpenAICompatibleModelsFailure,
    fetch_openai_compatible_models,
    openai_compatible_client_policy,
    resolve_host_addresses,
)
from account_pool.provider_services.openai_compatible.manifest import OPENAI_COMPATIBLE_MANIFEST


class OpenAICompatibleProviderService:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        resolve_host: HostResolver = resolve_host_addresses,
        discovery_client_factory: HttpClientFactory | None = None,
    ) -> None:
        policy: Final = None if client is None else openai_compatible_client_policy(client)
        self._client_policy = policy if not isinstance(policy, str) else None
        self._client_policy_error = None if not isinstance(policy, str) else policy
        self._client_timeout = None if client is None else client.timeout
        self._resolve_host = resolve_host
        self._discovery_client_factory = discovery_client_factory

    @property
    def manifest(self) -> ProviderServiceManifest:
        return OPENAI_COMPATIBLE_MANIFEST

    async def validate(self, request: ProviderValidationRequest) -> ProviderValidationResult:
        api_key: Final = request.api_key.get_secret_value()
        if self._client_policy_error is not None:
            return ProviderValidationResult(
                ok=False,
                provider_id=self.manifest.provider_id,
                normalized_api_base=request.api_base,
                group=request.group,
                key_fingerprint=_key_fingerprint(api_key),
                message=self._client_policy_error,
                failure_code=ProviderValidationFailureCode.INVALID_CONFIGURATION,
                capabilities=self.manifest.capabilities,
            )
        fetched: Final = await fetch_openai_compatible_models(
            api_base=request.api_base,
            api_key=api_key,
            resolve_host=self._resolve_host,
            client_factory=self._discovery_client_factory,
            client_policy=self._client_policy,
            timeout=self._client_timeout,
        )
        if isinstance(fetched, OpenAICompatibleModelsFailure):
            return ProviderValidationResult(
                ok=False,
                provider_id=self.manifest.provider_id,
                normalized_api_base=fetched.api_base,
                group=request.group,
                key_fingerprint=_key_fingerprint(api_key),
                message=fetched.message,
                failure_code=fetched.code,
                capabilities=self.manifest.capabilities,
            )
        return ProviderValidationResult(
            ok=True,
            provider_id=self.manifest.provider_id,
            normalized_api_base=fetched.api_base,
            group=request.group,
            key_fingerprint=_key_fingerprint(api_key),
            message=f"校验成功，发现 {len(fetched.models)} 个模型",
            capabilities=self.manifest.capabilities,
            models=tuple(ModelOffer(model=model) for model in fetched.models),
        )


def _key_fingerprint(api_key: str) -> str:
    return sha256(api_key.encode("utf-8")).hexdigest()[:12]
