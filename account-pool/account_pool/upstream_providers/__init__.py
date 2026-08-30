"""导出添加渠道使用的上游厂商模型发现注册表。"""

import httpx

from account_pool.upstream_providers.catalog import UPSTREAM_PROVIDER_DEFINITIONS
from account_pool.upstream_providers.registry import UpstreamProviderRegistry


def build_upstream_provider_registry(client: httpx.AsyncClient) -> UpstreamProviderRegistry:
    return UpstreamProviderRegistry(UPSTREAM_PROVIDER_DEFINITIONS, client)


__all__ = ("UpstreamProviderRegistry", "build_upstream_provider_registry")
