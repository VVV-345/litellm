"""定义 LiteLLM 自动授权公开 DTO，不导入 Manager 私有密文和浏览器控制凭证。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress, SecretStr, model_validator

Locale = Annotated[str, Field(pattern=r"^[a-z]{2,3}-[A-Z]{2}$")]


Stage = Literal[
    "queued",
    "starting",
    "running",
    "waiting",
    "manual",
    "callback_pending",
    "validating",
    "verifying",
    "verification_failed",
    "completed",
    "cancelled",
    "failed",
    "expired",
]


class AutomationSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    enabled: bool = False
    total_timeout_seconds: int = Field(default=600, ge=60, le=600)
    human_timeout_seconds: int = Field(default=300, ge=30, le=300)

    @model_validator(mode="after")
    def bounded_human_wait(self) -> AutomationSettings:
        if self.human_timeout_seconds > self.total_timeout_seconds:
            raise ValueError("human wait must be inside the total deadline")
        return self


class ProfileInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = Field(min_length=1, max_length=80, pattern=r".*\S.*")
    enabled: bool = True
    locale: Locale = "en-US"
    auto_match_proxy: bool = True
    os: Literal["linux", "windows", "macos"] = "linux"


class ProfileUpdate(ProfileInput):
    version: int = Field(ge=0)


class ProxyLocation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    exit_ip: IPvAnyAddress
    country_code: str = Field(pattern=r"^[A-Z]{2}$")
    country: str = Field(min_length=1, max_length=200)
    region: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=200)
    timezone: str = Field(min_length=1, max_length=80)


class ProfileView(ProfileInput):
    auto_match_proxy: bool = False
    id: UUID
    version: int = Field(default=0, ge=0)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    bound_environment_id: UUID | None = None
    location: ProxyLocation | None = None
    matched_locale: str | None = None
    reservation: bool = False
    login_saved: bool = False


class AutomationDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = Field(min_length=1, max_length=80, pattern=r".*\S.*")
    proxy_profile_id: str = Field(min_length=1, max_length=120)


class JobInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: UUID
    environment_id: UUID
    credentials: str = Field(min_length=1, max_length=8192, repr=False)
    model: str | None = Field(default=None, min_length=1, max_length=256)
    draft: AutomationDraft | None = None
    reuse_profile_id: UUID | None = None


class JobView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: UUID
    environment_id: UUID
    profile_id: UUID
    stage: Stage = "queued"
    message: str = "等待开启自动化后串行执行"
    proxy_profile_id: str
    configuration_version: int
    browser_session_id: UUID | None = None
    callback_consumed: bool = False
    credentials_saved: bool = False
    draft_pending: bool = False
    cleanup_pending: bool = False
    model: str | None = None
    chat_verified: bool = False
    responses_verified: bool = False
    cancel_requested: bool = False
    attempt: int = 0
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    human_expires_at: datetime | None = None


class AutomationView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: int
    settings: AutomationSettings
    profiles: tuple[ProfileView, ...]
    jobs: tuple[JobView, ...]
    checked_at: datetime | None
    image_checked: bool
    locale_presets: tuple[Locale, ...] | None = None


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: int = Field(ge=0)
    settings: AutomationSettings


class JobAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    action: Literal["cancel", "retry", "takeover", "resume", "cleanup"]
    model: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def retry_model_only(self) -> JobAction:
        if self.model is not None and self.action != "retry":
            raise ValueError("a model can only be selected when retrying verification")
        return self


class AutomationScreen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: UUID
    environment_id: UUID
    status: Literal["active"] = "active"
    created_at: datetime
    expires_at: datetime
    ticket: str = Field(repr=False)


class LoginScriptFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    path: str = Field(min_length=1, max_length=160)
    data: str = Field(max_length=87_384, repr=False)


class LoginScriptPackage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    entrypoint: str = Field(min_length=1, max_length=160)
    files: tuple[LoginScriptFile, ...] = Field(min_length=1, max_length=64, repr=False)


class LoginScriptUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    publisher_key: SecretStr = Field(min_length=32, max_length=128)
    package: LoginScriptPackage | None = Field(repr=False)


class LoginScriptInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    entrypoint: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    file_count: int


class LoginScriptStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    upload_enabled: bool = False
    active: LoginScriptInfo | None = None
    invalid: bool = False
