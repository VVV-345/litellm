"""定义邮箱换绑的公开代理 DTO，不包含 Manager 邮箱密文与控制凭据。"""

from datetime import datetime
from typing import Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from litellm.proxy.management_endpoints.account_pool_automation_models import LoginScriptPackage

MailMethod: TypeAlias = Literal["microsoft", "imap", "manual"]
BindingStage: TypeAlias = Literal[
    "awaiting_confirmation",
    "draining",
    "starting",
    "changing",
    "waiting_code",
    "manual",
    "uncertain",
    "email_changed",
    "reauthorizing",
    "verification_failed",
    "completed",
    "cancelled",
    "failed",
]


class MailboxView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: UUID
    email: str
    method: MailMethod
    status: Literal["available", "reserved", "bound"] = "available"
    job_id: UUID | None = None
    environment_id: UUID | None = None
    bound_at: datetime | None = None


class BindingView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: UUID
    environment_id: UUID
    mailbox_id: UUID
    old_email: str
    target_email: str
    stage: BindingStage = "awaiting_confirmation"
    version: int = 0
    message: str = "请核对原卡片与目标邮箱，确认后才会暂停卡片并换绑"
    irreversible_started: bool = False
    email_changed: bool = False
    browser_session_id: UUID | None = None
    oauth_job_id: UUID | None = None
    confirmed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    confirmation_expires_at: datetime


class BindingPoolView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: int
    mailboxes: tuple[MailboxView, ...]
    jobs: tuple[BindingView, ...]


class PoolUpload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    content: str = Field(min_length=1, max_length=1048576, repr=False)
    filename: str = Field(default="mailboxes.csv", max_length=180)


class PoolImportResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    view: BindingPoolView
    added: int
    existing: int


class ProposeBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: UUID
    environment_id: UUID
    mailbox_id: UUID | None = None


class ConfirmBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: int = Field(ge=0)
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value: object) -> Literal[True]:
        if value is not True:
            raise ValueError("explicit confirmation is required")
        return True


class BindingAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    action: Literal["cancel", "retry", "takeover", "resume", "reconcile"]


class MailboxCode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    code: str = Field(pattern=r"^[0-9]{6}$", repr=False)


class RebindingScriptUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    package: LoginScriptPackage | None = Field(repr=False)
