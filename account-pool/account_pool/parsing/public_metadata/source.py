"""注册无需凭证的公开元数据来源，并按 Provider 分发安全请求。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from account_pool.domain.provider_source import ProviderValidationResult
from account_pool.parsing.public_metadata.models import PublicMetadataChannel

PublicMetadataFetcher = Callable[[PublicMetadataChannel], Awaitable[ProviderValidationResult]]


@dataclass(frozen=True, slots=True)
class RegisteredPublicMetadataSource:
    provider_ids: tuple[str, ...]
    parser_id: str
    fetch: PublicMetadataFetcher


class PublicMetadataSourceRegistry:
    def __init__(self, sources: Iterable[RegisteredPublicMetadataSource]) -> None:
        resolved: Final = tuple(sources)
        entries: Final = tuple((provider_id, source) for source in resolved for provider_id in source.provider_ids)
        by_provider: Final = {provider_id: source for provider_id, source in entries}
        if len(by_provider) != len(entries):
            raise ValueError("public metadata provider IDs must be unique")
        self._by_provider = MappingProxyType(by_provider)

    @property
    def provider_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_provider))

    def resolve(self, provider_id: str) -> RegisteredPublicMetadataSource | None:
        return self._by_provider.get(provider_id)
