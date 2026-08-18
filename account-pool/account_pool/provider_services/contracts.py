"""声明所有渠道模块必须实现的最小协议。"""

from __future__ import annotations

from typing import Protocol

from account_pool.domain.provider_source import (
    ProviderServiceManifest,
    ProviderValidationRequest,
    ProviderValidationResult,
)


class ProviderService(Protocol):
    @property
    def manifest(self) -> ProviderServiceManifest: ...

    async def validate(self, request: ProviderValidationRequest) -> ProviderValidationResult: ...
