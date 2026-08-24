"""把 New API 网关的模型发现与倍率价格转换为统一渠道校验结果。"""

from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from typing import Final

import httpx

from account_pool.domain.provider_source import (
    MeteredPriceOffer,
    ModelOffer,
    ProviderServiceManifest,
    ProviderValidationRequest,
    ProviderValidationResult,
)
from account_pool.provider_services.new_api.client import (
    HostResolver,
    HttpClientFactory,
    NewApiDiscoveryFailure,
    NewApiPricedModel,
    fetch_new_api_discovery,
    resolve_host_addresses,
)
from account_pool.provider_services.new_api.manifest import NEW_API_MANIFEST


class NewApiProviderService:
    def __init__(
        self,
        client: httpx.AsyncClient,
        resolve_host: HostResolver = resolve_host_addresses,
        discovery_client_factory: HttpClientFactory | None = None,
    ) -> None:
        self._client = client
        self._resolve_host = resolve_host
        self._discovery_client_factory = discovery_client_factory

    @property
    def manifest(self) -> ProviderServiceManifest:
        return NEW_API_MANIFEST

    async def validate(self, request: ProviderValidationRequest) -> ProviderValidationResult:
        api_key: Final = request.api_key.get_secret_value()
        fetched: Final = await fetch_new_api_discovery(
            client=self._client,
            api_base=request.api_base,
            api_key=api_key,
            resolve_host=self._resolve_host,
            username=None if request.username is None else request.username.get_secret_value(),
            password=None if request.password is None else request.password.get_secret_value(),
            discovery_client_factory=self._discovery_client_factory,
        )
        if isinstance(fetched, NewApiDiscoveryFailure):
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
            message=f"校验成功，发现 {len(fetched.models)} 个模型与 {len(fetched.pricing)} 条倍率价格",
            pricing_failure_code=fetched.pricing_failure_code,
            capabilities=self.manifest.capabilities,
            models=tuple(ModelOffer(model=model) for model in fetched.models),
            pricing=tuple(
                _price_offer(priced, request.group)
                for priced in fetched.pricing
                if priced.model in frozenset(fetched.models)
            ),
        )


def _price_offer(priced: NewApiPricedModel, group: str | None) -> MeteredPriceOffer:
    entry: Final = priced.entry
    return MeteredPriceOffer(
        provider_model_id=priced.model,
        group_name=group,
        currency="RATIO",
        unit="multiplier",
        input_price=_positive_or(entry.model_ratio, Decimal("1")),
        output_price=_positive_or(entry.completion_ratio, Decimal("1")),
        cache_read_price=_positive_or_none(entry.cache_ratio),
        cache_write_price=_positive_or_none(entry.cache_write_ratio),
        group_multiplier=_positive_or(entry.group_ratio, Decimal("1")),
    )


def _to_decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _positive_or(value: float | None, default: Decimal) -> Decimal:
    parsed: Final = _to_decimal(value)
    return default if parsed is None or parsed <= 0 else parsed


def _positive_or_none(value: float | None) -> Decimal | None:
    parsed: Final = _to_decimal(value)
    return None if parsed is None or parsed <= 0 else parsed


def _key_fingerprint(api_key: str) -> str:
    return sha256(api_key.encode("utf-8")).hexdigest()[:12]
