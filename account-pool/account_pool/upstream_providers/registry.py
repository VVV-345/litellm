"""组织厂商目录，并返回资源侧报告的原始模型标识。"""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType
from typing import Final

import httpx

from account_pool.provider_services.safe_http import ValidatedAddressClientPolicy, validated_address_client_policy
from account_pool.upstream_providers.catalog import UpstreamProviderDefinition
from account_pool.upstream_providers.client import (
    HostResolver,
    HttpClientFactory,
    UpstreamModelsFailure,
    fetch_upstream_models,
    resolve_host_addresses,
)
from account_pool.upstream_providers.models import (
    UpstreamModelDiscoveryFailureCode,
    UpstreamModelDiscoveryRequest,
    UpstreamModelDiscoveryResult,
    UpstreamProviderManifest,
)


class UpstreamProviderRegistry:
    def __init__(
        self,
        definitions: Iterable[UpstreamProviderDefinition],
        client: httpx.AsyncClient | None = None,
        resolve_host: HostResolver = resolve_host_addresses,
        discovery_client_factory: HttpClientFactory | None = None,
    ) -> None:
        resolved_definitions: Final = tuple(definitions)
        definition_map: Final = {definition.manifest.provider_id: definition for definition in resolved_definitions}
        if len(definition_map) != len(resolved_definitions):
            raise ValueError("upstream provider ids must be unique")
        policy: Final = None if client is None else validated_address_client_policy(client)
        self._definitions = MappingProxyType(definition_map)
        self._client_policy: Final[ValidatedAddressClientPolicy | None] = policy if not isinstance(policy, str) else None
        self._client_policy_error: Final[str | None] = None if not isinstance(policy, str) else policy
        self._client_timeout: Final[httpx.Timeout | None] = None if client is None else client.timeout
        self._resolve_host = resolve_host
        self._discovery_client_factory = discovery_client_factory

    def manifests(self) -> tuple[UpstreamProviderManifest, ...]:
        return tuple(definition.manifest for definition in self._definitions.values())

    async def discover(self, request: UpstreamModelDiscoveryRequest) -> UpstreamModelDiscoveryResult:
        definition: Final = self._definitions.get(request.provider_id)
        if definition is None:
            return UpstreamModelDiscoveryResult(
                ok=False,
                provider_id=request.provider_id,
                normalized_api_base=request.api_base,
                message="未注册该上游厂商",
                failure_code=UpstreamModelDiscoveryFailureCode.UNSUPPORTED_PROVIDER,
            )
        if self._client_policy_error is not None:
            return UpstreamModelDiscoveryResult(
                ok=False,
                provider_id=request.provider_id,
                normalized_api_base=request.api_base,
                message=self._client_policy_error,
                failure_code=UpstreamModelDiscoveryFailureCode.INVALID_CONFIGURATION,
            )
        fetched: Final = await fetch_upstream_models(
            protocol=definition.protocol,
            api_base=request.api_base,
            default_api_base=definition.manifest.default_api_base,
            api_key=request.api_key.get_secret_value(),
            resolve_host=self._resolve_host,
            client_factory=self._discovery_client_factory,
            client_policy=self._client_policy,
            timeout=self._client_timeout,
        )
        if isinstance(fetched, UpstreamModelsFailure):
            return UpstreamModelDiscoveryResult(
                ok=False,
                provider_id=request.provider_id,
                normalized_api_base=fetched.api_base,
                message=fetched.message,
                failure_code=fetched.code,
            )
        return UpstreamModelDiscoveryResult(
            ok=True,
            provider_id=request.provider_id,
            normalized_api_base=fetched.api_base,
            message=f"已获取 {len(fetched.models)} 个资源侧模型",
            models=fetched.models,
        )
