"""定义添加渠道时按厂商获取上游模型的独立数据契约。"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, SecretStr

from account_pool.models import FrozenModel


class UpstreamModelDiscoveryFailureCode(StrEnum):
    INVALID_CONFIGURATION = "invalid_configuration"
    AUTHENTICATION = "authentication"
    TRANSPORT = "transport"
    UPSTREAM_RESPONSE = "upstream_response"
    NO_MODELS = "no_models"
    UNSUPPORTED_PROVIDER = "unsupported_provider"


class UpstreamProviderManifest(FrozenModel):
    provider_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    default_api_base: str


class UpstreamModelDiscoveryRequest(FrozenModel):
    provider_id: str = Field(min_length=1)
    api_base: str = Field(min_length=1)
    api_key: SecretStr


class UpstreamModelDiscoveryResult(FrozenModel):
    ok: bool
    provider_id: str
    normalized_api_base: str
    message: str
    failure_code: UpstreamModelDiscoveryFailureCode | None = None
    models: tuple[str, ...] = ()
