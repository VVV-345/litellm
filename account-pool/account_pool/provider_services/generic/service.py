"""把兼容模型与价格接口转换为通用解析器校验结果。"""

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
from account_pool.provider_services.generic.client import (
    CompatiblePricedModel,
    GenericDiscoveryFailure,
    HostResolver,
    HttpClientFactory,
    fetch_generic_discovery,
    resolve_host_addresses,
)
from account_pool.provider_services.generic.manifest import GENERIC_MANIFEST


class GenericProviderService:
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
        return GENERIC_MANIFEST

    async def validate(self, request: ProviderValidationRequest) -> ProviderValidationResult:
        api_key: Final = request.api_key.get_secret_value()
        fetched: Final = await fetch_generic_discovery(
            client=self._client,
            api_base=request.api_base,
            api_key=api_key,
            resolve_host=self._resolve_host,
            username=None if request.username is None else request.username.get_secret_value(),
            password=None if request.password is None else request.password.get_secret_value(),
            discovery_client_factory=self._discovery_client_factory,
        )
        if isinstance(fetched, GenericDiscoveryFailure):
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
        visible_models: Final = frozenset(fetched.models)
        pricing: Final = tuple(
            offer
            for priced in fetched.pricing
            for offer in (_price_offer(priced, request.group, fetched.group_ratios),)
            if priced.model in visible_models and offer is not None
        )
        return ProviderValidationResult(
            ok=True,
            provider_id=self.manifest.provider_id,
            normalized_api_base=fetched.api_base,
            group=request.group,
            key_fingerprint=_key_fingerprint(api_key),
            message=f"校验成功，发现 {len(fetched.models)} 个模型与 {len(pricing)} 条价格",
            pricing_failure_code=fetched.pricing_failure_code,
            capabilities=self.manifest.capabilities,
            models=tuple(ModelOffer(model=model) for model in fetched.models),
            pricing=pricing,
        )


def _price_offer(
    priced: CompatiblePricedModel,
    group: str | None,
    group_ratios: dict[str, float],
) -> MeteredPriceOffer | None:
    entry: Final = priced.entry
    multiplier: Final = _group_multiplier(group, group_ratios, entry.group_ratio)
    if entry.quota_type == 1:
        return None
    if entry.model_ratio is None and entry.completion_ratio is None:
        return None
    return MeteredPriceOffer(
        provider_model_id=priced.model,
        group_name=group,
        currency="RATIO",
        unit="multiplier",
        input_price=_to_decimal(entry.model_ratio),
        output_price=_to_decimal(entry.completion_ratio),
        cache_read_price=_to_decimal(entry.cache_ratio),
        cache_write_price=_to_decimal(entry.cache_write_ratio),
        group_multiplier=multiplier,
    )


def _to_decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _group_multiplier(
    group: str | None,
    group_ratios: dict[str, float],
    entry_multiplier: float | None,
) -> Decimal:
    selected: Final = entry_multiplier if entry_multiplier is not None else None if group is None else group_ratios.get(group)
    parsed: Final = _to_decimal(selected)
    return Decimal("1") if parsed is None or parsed <= 0 else parsed


def _key_fingerprint(api_key: str) -> str:
    return sha256(api_key.encode("utf-8")).hexdigest()[:12]
