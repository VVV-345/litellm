"""本模块定义环境生命周期、配置、额度窗口和公开响应的领域模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class EnvironmentStatus(StrEnum):
    PROVISIONING = "provisioning"
    AWAITING_AUTHORIZATION = "awaiting_authorization"
    VALIDATING = "validating"
    READY = "ready"
    COOLING_DOWN = "cooling_down"
    DISABLED = "disabled"
    ERROR = "error"
    DELETING = "deleting"


class Provider(StrEnum):
    OPENAI = "openai"


class ProxyMode(StrEnum):
    DEFAULT_GATEWAY = "default_gateway"
    PROFILE = "profile"


class QuotaWindow(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    used_percent: float = Field(ge=0, le=100)
    remaining_percent: float = Field(ge=0, le=100)
    window_minutes: int = Field(gt=0)
    resets_at: datetime | None = None


class QuotaSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    observed_at: datetime | None = None
    plan_type: str | None = None
    windows: tuple[QuotaWindow, ...] = ()


class ModelQuotaSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    quota: QuotaSnapshot


class EnvironmentRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    version: int = Field(default=0, ge=0)
    desired_configuration_version: int = Field(default=0, ge=0)
    observed_configuration_version: int = Field(default=0, ge=0)
    name: str
    provider: Provider
    status: EnvironmentStatus
    configuration_pending: bool = False
    enabled: bool
    manual_cooldown: bool
    concurrency_limit: int
    proxy_mode: ProxyMode
    proxy_profile_id: str | None
    available_models: tuple[str, ...]
    enabled_models: tuple[str, ...]
    auth_file_name: str | None
    auth_index: str | None
    quota: QuotaSnapshot
    model_quotas: tuple[ModelQuotaSnapshot, ...] = ()
    cooldown_until: datetime | None
    oauth_state: str | None
    oauth_expires_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class EnvironmentView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    version: int = Field(ge=0)
    name: str
    provider: Provider
    status: EnvironmentStatus
    configuration_pending: bool
    enabled: bool
    manual_cooldown: bool
    concurrency_limit: int
    proxy_mode: ProxyMode
    proxy_profile_id: str | None
    available_models: tuple[str, ...]
    enabled_models: tuple[str, ...]
    quota: QuotaSnapshot
    model_quotas: tuple[ModelQuotaSnapshot, ...] = ()
    cooldown_until: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class GatewayEnvironment(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    routable: bool
    concurrency_limit: int
    enabled_models: tuple[str, ...]
    api_base: str
    api_key: str


class CreateEnvironmentRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: Annotated[str, Field(min_length=1, max_length=80)]
    provider: Provider = Provider.OPENAI

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized: Final = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized


class UpdateEnvironmentRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int = Field(ge=0)
    name: Annotated[str, Field(min_length=1, max_length=80)]
    concurrency_limit: int = Field(ge=1, le=1000)
    enabled: bool
    manual_cooldown: bool
    proxy_mode: ProxyMode
    proxy_profile_id: str | None = Field(default=None, max_length=120)
    enabled_models: tuple[str, ...]

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized: Final = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized

    @field_validator("enabled_models")
    @classmethod
    def normalize_models(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: Final = tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
        return normalized

    @model_validator(mode="after")
    def validate_proxy_profile(self) -> UpdateEnvironmentRequest:
        if self.proxy_mode == ProxyMode.PROFILE and not self.proxy_profile_id:
            raise ValueError("proxy_profile_id is required for profile mode")
        if self.proxy_mode == ProxyMode.DEFAULT_GATEWAY and self.proxy_profile_id is not None:
            return self.model_copy(update={"proxy_profile_id": None})
        return self


class AuthorizationView(BaseModel):
    model_config = ConfigDict(frozen=True)

    environment: EnvironmentView
    authorization_url: HttpUrl
    ssh_command: str
    expires_at: datetime


class OAuthCallback(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str | None = None
    state: str
    error: str | None = None
    error_description: str | None = None

    @model_validator(mode="after")
    def require_result(self) -> OAuthCallback:
        if self.code is None and self.error is None and self.error_description is None:
            raise ValueError("code or error is required")
        return self


class ProxyProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^[a-zA-Z0-9_.-]+$", max_length=120)
    name: str = Field(min_length=1, max_length=120)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_view(record: EnvironmentRecord) -> EnvironmentView:
    public_record: Final = record.model_dump(
        exclude={"oauth_state", "oauth_expires_at", "auth_file_name", "auth_index"}
    )
    return EnvironmentView.model_validate(public_record)
