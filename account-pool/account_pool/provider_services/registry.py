"""集中注册渠道模块，公共层不包含任何按渠道名称分支。"""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType
from typing import Final

from account_pool.domain.provider_source import (
    ProviderServiceManifest,
    ProviderValidationRequest,
    ProviderValidationResult,
)
from account_pool.provider_services.contracts import ProviderService


class ProviderServiceRegistry:
    def __init__(self, services: Iterable[ProviderService]) -> None:
        resolved_services: Final = tuple(services)
        service_map: Final = {service.manifest.provider_id: service for service in resolved_services}
        if len(service_map) != len(resolved_services):
            raise ValueError("provider service ids must be unique")
        self._services = MappingProxyType(service_map)

    def manifests(self) -> tuple[ProviderServiceManifest, ...]:
        return tuple(service.manifest for service in self._services.values())

    async def validate(self, request: ProviderValidationRequest) -> ProviderValidationResult:
        service: Final = self._services.get(request.provider_id)
        if service is None:
            return ProviderValidationResult(
                ok=False,
                provider_id=request.provider_id,
                normalized_api_base=request.api_base,
                group=request.group,
                key_fingerprint=None,
                message="未注册该渠道服务",
                capabilities=(),
            )
        return await service.validate(request)
