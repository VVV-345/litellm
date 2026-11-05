"""定义授权自动化的公开状态和私有密文，指纹配置不接收脚本或浏览器参数。"""

from __future__ import annotations

from datetime import datetime
from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from account_pool.isp_probe import ProxyLocation

Locale = Literal["en-US", "en-GB", "zh-CN"]
BrowserOS = Literal["linux", "windows", "macos"]
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
ACTIVE: Final = frozenset({"starting", "running", "waiting", "manual", "callback_pending", "validating", "verifying"})


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
    os: BrowserOS = "linux"


class ProfileUpdate(ProfileInput):
    version: int = Field(ge=0)


class ProfileView(ProfileInput):
    auto_match_proxy: bool = False
    id: UUID
    version: int = Field(default=0, ge=0)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    bound_environment_id: UUID | None = None
    location: ProxyLocation | None = None
    matched_locale: str | None = None


class StoredProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    item: ProfileView
    data: dict[str, JsonValue] = Field(repr=False)
    account_digest: str | None = Field(default=None, repr=False)


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


class StoredJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    item: JobView
    account_digest: str = Field(repr=False)
    credential_ciphertext: str = Field(repr=False)
    session_ciphertext: str | None = Field(default=None, repr=False)
    cleanup_pending: bool = False
    login_submitted: bool = False
    verification_started: bool = False


class SessionSecrets(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    worker_token: str = Field(min_length=32, repr=False)
    ticket: str = Field(repr=False)


class WorkerView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["prepared", "running", "waiting", "manual", "callback_received", "cancelled", "expired", "failed"]
    reason: (
        Literal[
            "manual_takeover",
            "account_verification_required",
            "stopped",
            "verification_required",
            "account_mismatch",
            "confirmation_required",
            "browser_driver_failed",
            "cancelled",
            "expired",
        ]
        | None
    ) = None
    expires_at: datetime


class AutomationState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: int = 0
    settings: AutomationSettings = Field(default_factory=AutomationSettings)
    profiles: tuple[StoredProfile, ...] = Field(default=(), max_length=100)
    jobs: tuple[StoredJob, ...] = Field(default=(), max_length=200)
    checked_image: str | None = None
    checked_at: datetime | None = None


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
