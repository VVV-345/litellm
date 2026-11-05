"""定义 LiteLLM 自动授权公开 DTO，不导入 Manager 私有密文和浏览器控制凭证。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress, model_validator

Locale = Literal["en-US", "en-GB", "zh-CN"]


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


class JobInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: UUID
    environment_id: UUID
    credentials: str = Field(min_length=1, max_length=8192, repr=False)
    model: str | None = Field(default=None, min_length=1, max_length=256)


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


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: int = Field(ge=0)
    settings: AutomationSettings


class JobAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    action: Literal["cancel", "retry", "takeover", "resume"]
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
