from __future__ import annotations

from hashlib import sha256
from typing import Final

import httpx

from account_pool.domain.provider_source import (
    ModelOffer,
    ProviderServiceManifest,
    ProviderValidationRequest,
    ProviderValidationResult,
)
from account_pool.provider_services.openai_compatible.client import (
    HostResolver,
    OpenAICompatibleModelsFailure,
    fetch_openai_compatible_models,
    resolve_host_addresses,
)
from account_pool.provider_services.openai_compatible.manifest import OPENAI_COMPATIBLE_MANIFEST


class OpenAICompatibleProviderService:
    def __init__(
        self,
        client: httpx.AsyncClient,
        resolve_host: HostResolver = resolve_host_addresses,
    ) -> None:
        self._client = client
        self._resolve_host = resolve_host

    @property
    def manifest(self) -> ProviderServiceManifest:
        return OPENAI_COMPATIBLE_MANIFEST

    async def validate(self, request: ProviderValidationRequest) -> ProviderValidationResult:
        api_key: Final = request.api_key.get_secret_value()
        fetched: Final = await fetch_openai_compatible_models(
            client=self._client,
            api_base=request.api_base,
            api_key=api_key,
            resolve_host=self._resolve_host,
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
