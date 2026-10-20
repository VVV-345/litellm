"""本模块定义反馈内容、状态和限权凭证契约，不执行反馈正文中的指令。"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True)]
FeedbackStatus = Literal["pending", "in_progress", "pending_verification", "resolved"]
FeedbackPriority = Literal["high", "normal", "low"]
FeedbackAction = Literal["claim", "submit_fix", "resolve", "reopen", "release", "note"]
MAX_FEEDBACK_ATTACHMENT_SIZE = 10 * 1024 * 1024
FeedbackAttachmentContentType = Literal["image/png", "image/jpeg", "image/webp", "application/pdf", "text/plain"]


class FeedbackModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FeedbackAttachmentInfo(FeedbackModel):
    id: UUID
    filename: Text = Field(min_length=1, max_length=255)
    content_type: FeedbackAttachmentContentType
    size: int = Field(ge=1, le=MAX_FEEDBACK_ATTACHMENT_SIZE)


class FeedbackAttachmentRecord(FeedbackAttachmentInfo):
    issue_id: UUID
    content: bytes = Field(repr=False)


class FeedbackCreate(FeedbackModel):
    title: Text = Field(min_length=1, max_length=200)
    description: Text = Field(min_length=1, max_length=8000)
    steps: Text = Field(default="", max_length=8000)
    module: Text = Field(default="", max_length=80)
    page_path: Text = Field(default="", max_length=512)
    discovered_version: Text = Field(default="", max_length=160)
    priority: FeedbackPriority = "normal"

    @field_validator("page_path")
    @classmethod
    def path_only(cls, value: str) -> str:
        if value and (not value.startswith("/") or value.startswith("//") or any(c in value for c in "?#\\\r\n")):
            raise ValueError("Only a page path without query or fragment is allowed")
        return value


class FeedbackEdit(FeedbackCreate):
    expected_revision: int = Field(ge=1)


class FeedbackMutation(FeedbackModel):
    action: FeedbackAction
    expected_revision: int = Field(ge=1)
    summary: Text = Field(default="", max_length=8000)
    commit: Text = Field(default="", max_length=64)
    verification: Text = Field(default="", max_length=16000)
    deployed_version: Text = Field(default="", max_length=160)


class FeedbackEvent(FeedbackModel):
    at: AwareDatetime
    actor: str
    action: Literal["create", "edit", "attachment", "claim", "submit_fix", "resolve", "reopen", "release", "note"]
    summary: str = ""
    commit: str = ""
    verification: str = ""
    deployed_version: str = ""


class FeedbackIssue(FeedbackCreate):
    id: UUID
    revision: int = Field(ge=1)
    status: FeedbackStatus = "pending"
    created_at: AwareDatetime
    updated_at: AwareDatetime
    assigned_to: str | None = None
    resolution: str = ""
    commit: str = ""
    verification: str = ""
    deployed_version: str = ""
    events: tuple[FeedbackEvent, ...] = ()
    attachments: tuple[FeedbackAttachmentInfo, ...] = ()


class FeedbackAgentAttachmentRead(FeedbackModel):
    token: str = Field(min_length=1, max_length=256, repr=False)


class FeedbackStats(FeedbackModel):
    pending: int = 0
    in_progress: int = 0
    pending_verification: int = 0
    resolved: int = 0


class FeedbackPage(FeedbackModel):
    items: tuple[FeedbackIssue, ...]
    total: int
    stats: FeedbackStats


class FeedbackQuery(FeedbackModel):
    status: Literal["all", "unresolved", "pending", "in_progress", "pending_verification", "resolved"] = "all"
    q: Text = Field(default="", max_length=200)
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class FeedbackTokenCreate(FeedbackModel):
    name: Text = Field(min_length=1, max_length=80)
    expires_in_days: int = Field(default=30, ge=1, le=365)


class FeedbackTokenView(FeedbackModel):
    id: UUID
    name: str
    created_at: AwareDatetime
    expires_at: AwareDatetime
    revoked_at: AwareDatetime | None = None


class FeedbackTokenIssue(FeedbackModel):
    record: FeedbackTokenView
    token: str = Field(repr=False)


class FeedbackTokenRecord(FeedbackTokenView):
    token_hash: str = Field(repr=False)


class FeedbackAgentCommand(FeedbackModel):
    token: str = Field(min_length=1, max_length=256, repr=False)
    operation: Literal["list", "get", "mutate"]
    issue_id: UUID | None = None
    query: FeedbackQuery = Field(default_factory=FeedbackQuery)
    mutation: FeedbackMutation | None = None
