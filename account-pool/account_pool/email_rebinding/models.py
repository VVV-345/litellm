"""定义邮箱池和换绑进度，公开视图不包含密码、令牌或浏览器票据。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

MailMethod = Literal["microsoft", "imap", "manual"]
BindingStage = Literal[
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


class MailboxSecret(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(default="", max_length=4096, repr=False)
    client_id: str = Field(default="", max_length=512, repr=False)
    refresh_token: str = Field(default="", max_length=16384, repr=False)
    access_token: str = Field(default="", max_length=16384, repr=False)
    access_expires_at: datetime | None = None
    method: MailMethod = "manual"
    refresh_pending: bool = False


class MailboxView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: UUID
    email: str
    method: MailMethod
    status: Literal["available", "reserved", "bound"] = "available"
    job_id: UUID | None = None
    environment_id: UUID | None = None
    bound_at: datetime | None = None


class StoredMailbox(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    item: MailboxView
    digest: str = Field(repr=False)
    ciphertext: str = Field(repr=False)


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


class StoredBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    item: BindingView
    profile_id: UUID
    card_version: int
    proxy_profile_id: str
    configuration_version: int
    profile_digest: str
    original_login_ciphertext: str = Field(repr=False)
    worker_ciphertext: str | None = Field(default=None, repr=False)
    subject_ciphertext: str | None = Field(default=None, repr=False)
    verify_only: bool = False
    mail_poll_failed: bool = False
    code_requested_at: datetime | None = None


class BindingState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: int = 0
    mailboxes: tuple[StoredMailbox, ...] = Field(default=(), max_length=2000)
    jobs: tuple[StoredBinding, ...] = Field(default=(), max_length=200)


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
    action: Literal["cancel", "retry", "takeover", "resume", "reconcile", "abort"]


class MailboxCode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    code: str = Field(pattern=r"^[0-9]{6}$", repr=False)


class RebindingWorkerView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["prepared", "running", "waiting", "manual", "callback_received", "cancelled", "expired", "failed"]
    reason: str | None = Field(default=None, max_length=80)
    expires_at: datetime
    step: Literal["login", "awaiting_commit", "changing", "waiting_code", "verifying", "changed"] = "login"
    source_email: str | None = Field(default=None, max_length=320, repr=False)
    subject: str | None = Field(default=None, max_length=256, repr=False)
    target_email: str | None = Field(default=None, max_length=320, repr=False)
