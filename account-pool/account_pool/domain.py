"""本模块定义环境生命周期、配置、额度窗口和公开响应的领域模型。"""

from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Final, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


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


class ChannelKind(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    CLIPROXYAPI = "cliproxyapi"


class SupplierKind(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    OPENAI_CODEX = "openai_codex"
    ANTHROPIC_CLAUDE = "anthropic_claude"
    GOOGLE_ANTIGRAVITY = "google_antigravity"
    KIMI = "kimi"
    XAI = "xai"
    GEMINI = "gemini"
    GEMINI_INTERACTIONS = "gemini_interactions"
    VERTEX = "vertex"


class AuthorizationFlow(StrEnum):
    BROWSER_OAUTH = "browser_oauth"
    DEVICE_CODE = "device_code"
    DIRECT_CREDENTIAL = "direct_credential"


AuthorizationInstructionFlow = Literal[AuthorizationFlow.BROWSER_OAUTH, AuthorizationFlow.DEVICE_CODE]


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


class EnvironmentConfiguration(BaseModel):
    """环境需要收敛到所选渠道的完整配置快照，不包含任何密钥。"""

    model_config = ConfigDict(frozen=True)

    name: Annotated[str, Field(min_length=1, max_length=80)]
    concurrency_limit: int = Field(ge=1, le=1000)
    enabled: bool
    manual_cooldown: bool
    proxy_mode: ProxyMode
    proxy_profile_id: str | None = Field(default=None, max_length=120)
    enabled_models: tuple[str, ...] = ()
    proxy_url: str = ""
    credential_enabled: bool = True

    @model_validator(mode="after")
    def normalize_proxy_mode(self) -> EnvironmentConfiguration:
        if self.proxy_mode is ProxyMode.DEFAULT_GATEWAY and self.proxy_profile_id is not None:
            return self.model_copy(update={"proxy_profile_id": None, "proxy_url": ""})
        return self


class OpenAICompatibleKeyRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: str = Field(min_length=1, max_length=4096, repr=False)
    proxy_profile_id: str | None = Field(default=None, max_length=120)
    weight: int = Field(default=1, ge=1, le=10000)


class DirectAPIKeyCredentialRequest(BaseModel):
    """CLIProxyAPI 直连 API Key 创建载荷，密钥只允许在本次请求中使用。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: str = Field(min_length=1, max_length=4096, repr=False)
    prefix: str = Field(default="", max_length=120)
    priority: int = Field(default=0, ge=-10000, le=10000)
    weight: int = Field(default=1, ge=1, le=1000000)
    base_url: AnyHttpUrl | None = None
    headers: tuple[tuple[str, str], ...] = Field(default=(), max_length=100)

    @field_validator("api_key", "prefix")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, values: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        blocked: Final = frozenset(
            ("authorization", "proxy-authorization", "host", "content-length", "transfer-encoding", "connection")
        )
        normalized: Final = tuple((name.strip().lower(), value) for name, value in values if name.strip())
        if any(name in blocked for name, _ in normalized):
            raise ValueError("headers contain a protected transport header")
        if any(not name.replace("-", "").isalnum() for name, _ in normalized):
            raise ValueError("headers contain an invalid name")
        return tuple(dict.fromkeys(normalized))


class CreateDirectCredentialEnvironmentRequest(BaseModel):
    """创建单凭据 CLIProxyAPI 环境，不持久化 API Key 明文。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=80)]
    supplier: SupplierKind
    credential: DirectAPIKeyCredentialRequest
    operation_id: str | None = Field(default=None, max_length=160)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized: Final = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_supplier(self) -> CreateDirectCredentialEnvironmentRequest:
        if self.supplier not in (SupplierKind.GEMINI, SupplierKind.GEMINI_INTERACTIONS):
            raise ValueError("supplier does not accept a direct API key")
        return self


class CreateVertexEnvironmentRequest(BaseModel):
    """创建 Vertex 服务账号环境，服务账号正文由独立 multipart 字段传入。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=80)]
    location: str = Field(default="us-central1", pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    operation_id: str | None = Field(default=None, max_length=160)

    @field_validator("name", "location")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized: Final = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class OpenAICompatibleCredentialRequest(BaseModel):
    """新增卡片 API Key 的请求，密钥只在写入时接收。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=0)
    api_key: str = Field(min_length=1, max_length=4096, repr=False)
    proxy_profile_id: str | None = Field(default=None, max_length=120)
    weight: int = Field(default=1, ge=1, le=10000)


class OpenAICompatibleCredentialDeleteRequest(BaseModel):
    """按卡片版本和凭据索引删除 API Key，避免向客户端暴露密钥。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=0)
    credential_index: int = Field(ge=0, le=99)


class OpenAICompatibleCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: AnyHttpUrl
    prefix: str = Field(default="", max_length=120)
    priority: int = Field(default=0, ge=-10000, le=10000)
    test_model: str = Field(min_length=1, max_length=256)
    api_keys: tuple[OpenAICompatibleKeyRequest, ...] = Field(min_length=1, max_length=100)
    headers: tuple[tuple[str, str], ...] = Field(default=(), max_length=100)
    custom_models: tuple[str, ...] = Field(default=(), max_length=500)

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        normalized: Final = str(value).rstrip("/")
        parsed: Final = urlsplit(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
        ):
            raise ValueError("base_url must be a credential-free HTTP(S) origin")
        hostname: Final = parsed.hostname.rstrip(".").lower()
        if hostname in {
            "localhost",
            "localhost.localdomain",
            "ip6-localhost",
            "host.docker.internal",
            "metadata",
            "metadata.google.internal",
            "instance-data.ec2.internal",
        }:
            raise ValueError("base_url targets a blocked local or metadata host")
        try:
            address: Final = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and (
            address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise ValueError("base_url targets a blocked local network address")
        return AnyHttpUrl(normalized)

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, values: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        blocked: Final = frozenset(
            ("authorization", "proxy-authorization", "host", "content-length", "transfer-encoding", "connection")
        )
        normalized: Final = tuple((name.strip().lower(), value) for name, value in values if name.strip())
        if any(name in blocked for name, _ in normalized):
            raise ValueError("headers contain a protected transport header")
        if any(not name.replace("-", "").isalnum() for name, _ in normalized):
            raise ValueError("headers contain an invalid name")
        return tuple(dict.fromkeys(normalized))

    @field_validator("custom_models")
    @classmethod
    def normalize_models(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


class OpenAICompatibleCredential(BaseModel):
    model_config = ConfigDict(frozen=True, repr=False)

    api_key_ciphertext: str
    proxy_profile_id: str | None = None
    proxy_url: str | None = None
    weight: int = Field(default=1, ge=1, le=10000)


class OpenAICompatibleConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, repr=False)

    base_url: str
    prefix: str = ""
    priority: int = 0
    test_model: str
    credentials: tuple[OpenAICompatibleCredential, ...]
    headers_ciphertext: str | None = None
    custom_models: tuple[str, ...] = ()


class CleanupProgress(BaseModel):
    """删除流程的持久化检查点，允许 Manager 重启后从未完成步骤继续。"""

    model_config = ConfigDict(frozen=True)

    routes_removed: bool = False
    compose_removed: bool = False
    directory_removed: bool = False

    @property
    def complete(self) -> bool:
        return self.routes_removed and self.compose_removed and self.directory_removed


class EnvironmentRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    version: int = Field(default=0, ge=0)
    desired_state: EnvironmentStatus | None = None
    operation_id: str | None = Field(default=None, max_length=160)
    cleanup_progress: CleanupProgress = Field(default_factory=CleanupProgress)
    desired_configuration_version: int = Field(default=0, ge=0)
    observed_configuration_version: int = Field(default=0, ge=0)
    desired_configuration: EnvironmentConfiguration | None = None
    configuration_last_error: str | None = None
    name: str
    provider: Provider
    channel: ChannelKind = ChannelKind.CLIPROXYAPI
    supplier: SupplierKind = SupplierKind.OPENAI_CODEX
    openai_compatible: OpenAICompatibleConfiguration | None = None
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
    automatic_cooldown: bool = False
    oauth_state: str | None
    oauth_expires_at: datetime | None
    oauth_state_consumed_at: datetime | None = None
    oauth_state_signature: str | None = None
    oauth_provider_state: str | None = None
    oauth_authorization_url: str | None = None
    authorization_flow: AuthorizationFlow = AuthorizationFlow.BROWSER_OAUTH
    authorization_user_code: str | None = None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class EnvironmentView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    version: int = Field(ge=0)
    desired_state: EnvironmentStatus | None = None
    operation_id: str | None = None
    cleanup_progress: CleanupProgress = Field(default_factory=CleanupProgress)
    desired_configuration_version: int = Field(default=0, ge=0)
    observed_configuration_version: int = Field(default=0, ge=0)
    name: str
    provider: Provider
    channel: ChannelKind = ChannelKind.CLIPROXYAPI
    supplier: SupplierKind = SupplierKind.OPENAI_CODEX
    authorization_flow: AuthorizationFlow = AuthorizationFlow.BROWSER_OAUTH
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
    automatic_cooldown: bool = False
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class GatewayCredential(BaseModel):
    model_config = ConfigDict(frozen=True)

    api_key: str = Field(repr=False)
    proxy_url: str | None = None
    weight: int = Field(default=1, ge=1, le=10000)


class GatewayEnvironment(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    routable: bool
    concurrency_limit: int
    enabled_models: tuple[str, ...]
    api_base: str
    api_key: str = Field(repr=False)
    credentials: tuple[GatewayCredential, ...] = Field(default=(), repr=False)
    headers: tuple[tuple[str, str], ...] = Field(default=(), repr=False)
    model_prefix: str = ""
    custom_llm_provider: str = "openai"


class CreateEnvironmentRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: Annotated[str, Field(min_length=1, max_length=80)]
    provider: Provider = Provider.OPENAI
    channel: ChannelKind = ChannelKind.CLIPROXYAPI
    supplier: SupplierKind = SupplierKind.OPENAI_CODEX
    provider_family: str | None = Field(default=None, max_length=80)
    openai_compatible: OpenAICompatibleCreateRequest | None = None
    operation_id: str | None = Field(default=None, max_length=160)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized: Final = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_provider_family(self) -> CreateEnvironmentRequest:
        if self.provider_family == SupplierKind.OPENAI_COMPATIBLE.value and self.openai_compatible is None:
            raise ValueError("openai_compatible configuration is required")
        if self.openai_compatible is not None and self.provider_family != SupplierKind.OPENAI_COMPATIBLE.value:
            raise ValueError("openai_compatible configuration requires the openai_compatible provider family")
        return self


class UpdateEnvironmentRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int = Field(ge=0)
    operation_id: str | None = Field(default=None, max_length=160)
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
    flow: AuthorizationInstructionFlow
    authorization_url: HttpUrl
    ssh_command: str | None
    user_code: str | None
    expires_at: datetime


def configured_proxy_url(record: EnvironmentRecord) -> str:
    if record.proxy_mode is ProxyMode.DEFAULT_GATEWAY or record.desired_configuration is None:
        return ""
    return record.desired_configuration.proxy_url


def configuration_from_record(record: EnvironmentRecord, proxy_url: str = "") -> EnvironmentConfiguration:
    """从当前记录生成不含凭据的期望配置快照。"""
    return EnvironmentConfiguration(
        name=record.name,
        concurrency_limit=record.concurrency_limit,
        enabled=record.enabled,
        manual_cooldown=record.manual_cooldown,
        proxy_mode=record.proxy_mode,
        proxy_profile_id=record.proxy_profile_id,
        enabled_models=record.enabled_models,
        proxy_url=proxy_url,
        credential_enabled=record.enabled and not record.manual_cooldown,
    )


class OAuthCallback(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str | None = None
    state: str
    error: str | None = None
    error_description: str | None = None

    @model_validator(mode="after")
    def require_result(self) -> OAuthCallback:
        has_code: Final = self.code is not None and bool(self.code.strip())
        has_error: Final = any(
            value is not None and bool(value.strip()) for value in (self.error, self.error_description)
        )
        if has_code == has_error:
            raise ValueError("exactly one non-empty code or error is required")
        return self


class ProxyProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^[a-zA-Z0-9_.-]+$", max_length=120)
    name: str = Field(min_length=1, max_length=120)
    protocol: str | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_view(record: EnvironmentRecord) -> EnvironmentView:
    # 显式构造公开模型，避免未来新增内部字段时意外泄露 state、签名或代理凭据。
    return EnvironmentView(
        id=record.id,
        version=record.version,
        desired_state=record.desired_state or record.status,
        operation_id=record.operation_id,
        cleanup_progress=record.cleanup_progress,
        desired_configuration_version=record.desired_configuration_version,
        observed_configuration_version=record.observed_configuration_version,
        name=record.name,
        provider=record.provider,
        channel=record.channel,
        supplier=record.supplier,
        authorization_flow=record.authorization_flow,
        status=record.status,
        configuration_pending=record.configuration_pending,
        enabled=record.enabled,
        manual_cooldown=record.manual_cooldown,
        concurrency_limit=record.concurrency_limit,
        proxy_mode=record.proxy_mode,
        proxy_profile_id=record.proxy_profile_id,
        available_models=record.available_models,
        enabled_models=record.enabled_models,
        quota=record.quota,
        model_quotas=record.model_quotas,
        cooldown_until=record.cooldown_until,
        automatic_cooldown=record.automatic_cooldown,
        last_error=record.last_error,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
