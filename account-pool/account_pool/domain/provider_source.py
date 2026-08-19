"""定义渠道服务能力、校验输入与标准化发现结果。"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class FrozenModel(BaseModel):
    """渠道发现数据均作为不可变快照在各层之间传递。"""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ProviderCapability(StrEnum):
    CONNECTION = "connection"
    MODEL_DISCOVERY = "model_discovery"
    KEY_LISTING = "key_listing"
    ACCOUNT_BALANCE = "account_balance"
    SUBSCRIPTIONS = "subscriptions"
    PERIODIC_LIMITS = "periodic_limits"
    MODEL_PRICING = "model_pricing"


class CapabilityState(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


class ProviderValidationFailureCode(StrEnum):
    INVALID_CONFIGURATION = "invalid_configuration"
    AUTHENTICATION = "authentication"
    TRANSPORT = "transport"
    UPSTREAM_RESPONSE = "upstream_response"
    NO_MODELS = "no_models"
    UNSUPPORTED_PROVIDER = "unsupported_provider"


class ProviderCapabilityView(FrozenModel):
    capability: ProviderCapability
    state: CapabilityState
    message: str


class ProviderServiceManifest(FrozenModel):
    provider_id: str
    display_name: str
    default_api_base: str
    litellm_provider_prefix: str
    capabilities: tuple[ProviderCapabilityView, ...]


class ProviderValidationRequest(FrozenModel):
    provider_id: str = Field(min_length=1)
    api_base: str = Field(min_length=1)
    api_key: SecretStr
    group: str | None = None


class ModelOffer(FrozenModel):
    model: str
    input_price_per_million: float | None = Field(default=None, ge=0)
    output_price_per_million: float | None = Field(default=None, ge=0)
    currency: str | None = None
    pricing_source: str | None = None


class AccountBalance(FrozenModel):
    amount: float
    currency: str


class SubscriptionSnapshot(FrozenModel):
    name: str
    expires_at: str | None = None
    remaining: float | None = Field(default=None, ge=0)
    unit: str | None = None


class PeriodicLimitSnapshot(FrozenModel):
    window: str
    limit: float = Field(ge=0)
    remaining: float = Field(ge=0)
    resets_at: str | None = None


class ProviderValidationResult(FrozenModel):
    ok: bool
    provider_id: str
    normalized_api_base: str
    group: str | None
    key_fingerprint: str | None
    message: str
    failure_code: ProviderValidationFailureCode | None = None
    capabilities: tuple[ProviderCapabilityView, ...]
    models: tuple[ModelOffer, ...] = ()
    balance: AccountBalance | None = None
    subscriptions: tuple[SubscriptionSnapshot, ...] = ()
    limits: tuple[PeriodicLimitSnapshot, ...] = ()

    @model_validator(mode="after")
    def validate_failure_code(self) -> Self:
        if self.ok and self.failure_code is not None:
            raise ValueError("successful provider validation cannot have a failure code")
        if not self.ok and self.failure_code is None:
            raise ValueError("failed provider validation requires a failure code")
        return self
