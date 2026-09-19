"""定义独立的文件导入和 OAuth 上号协议，公开投影不包含密码或认证内容。"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

OnboardingSupplier = Literal["openai_codex", "anthropic_claude", "google_antigravity", "kimi", "xai"]
MailboxKind = Literal["outlook", "gmail", "mail"]
OnboardingState = Literal[
    "awaiting_mailbox",
    "standby",
    "queued",
    "running",
    "awaiting_authorization",
    "ready",
    "cooling_down",
    "disabled",
    "failed",
]


class OnboardingEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    label: str = Field(min_length=1, max_length=254)
    content: str = Field(default="", max_length=1024 * 1024, repr=False)
    mailbox_password: str = Field(default="", max_length=1024, repr=False)
    supplier_password: str = Field(default="", max_length=1024, repr=False)


class OnboardingImport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    job_id: UUID
    source: Literal["auth_file", "oauth"]
    supplier: OnboardingSupplier
    mailbox: MailboxKind = "mail"
    prepare_mailbox: bool = True
    entries: tuple[OnboardingEntry, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def bounded_payload(self) -> OnboardingImport:
        if sum(len(entry.content.encode()) for entry in self.entries) > 8 * 1024 * 1024:
            raise ValueError("每批认证文件总大小不得超过 8 MiB")
        return self


class OnboardingPreview(BaseModel):
    model_config = ConfigDict(frozen=True)
    index: int
    label: str
    status: Literal["valid", "invalid", "duplicate"]
    message: str


class OnboardingItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    job_id: UUID
    source: Literal["auth_file", "oauth"]
    supplier: OnboardingSupplier
    mailbox: MailboxKind | None = None
    label: str
    state: OnboardingState
    message: str
    card_id: UUID
    card_name: str | None = None
    models: tuple[str, ...] = ()
    attempts: int = 0
    created_at: AwareDatetime
    updated_at: AwareDatetime


class OnboardingImportResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    preview: tuple[OnboardingPreview, ...]
    items: tuple[OnboardingItem, ...]


class OnboardingAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    action: Literal["start", "retry", "pause", "mailbox_ready", "generate_password"]
    mailbox_password: str | None = Field(default=None, max_length=1024, repr=False)


class OnboardingSecrets(BaseModel):
    model_config = ConfigDict(frozen=True)
    mailbox_password: str = Field(repr=False)
    supplier_password: str = Field(repr=False)
    proposed_password: str | None = Field(default=None, repr=False)


class OnboardingTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    supplier: OnboardingSupplier
    model: str = Field(default="", max_length=160)
    enabled: bool = False
    count: int = Field(default=0, ge=0, le=100)


class OnboardingTargetView(OnboardingTarget):
    ready: int = 0
    in_progress: int = 0
    standby: int = 0
    awaiting_mailbox: int = 0
    shortage: int = 0


class OnboardingAuthorization(BaseModel):
    model_config = ConfigDict(frozen=True)
    flow: Literal["browser_oauth", "device_code"]
    authorization_url: str
    ssh_command: str | None
    user_code: str | None
    expires_at: AwareDatetime


class OnboardingSupplierOption(BaseModel):
    model_config = ConfigDict(frozen=True)
    supplier: str
    display_name: str
    authentication: str
    oauth: bool
    auth_file: bool
    description: str
