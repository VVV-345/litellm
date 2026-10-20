"""本模块编排反馈状态与认领规则，凭证只持久化摘要，写入使用版本条件更新。"""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Final, Protocol
from uuid import UUID, uuid4

from account_pool.feedback_attachments import FeedbackAttachmentValidationFailure, create_attachment
from account_pool.feedback_models import (
    FeedbackAttachmentInfo,
    FeedbackAttachmentRecord,
    FeedbackCreate,
    FeedbackEdit,
    FeedbackEvent,
    FeedbackIssue,
    FeedbackMutation,
    FeedbackPage,
    FeedbackQuery,
    FeedbackTokenCreate,
    FeedbackTokenIssue,
    FeedbackTokenRecord,
    FeedbackTokenView,
)


@dataclass(frozen=True, slots=True)
class FeedbackActor:
    id: str
    manager: bool


@dataclass(frozen=True, slots=True)
class FeedbackFailure:
    status: int
    code: str
    message: str


class FeedbackRepository(Protocol):
    async def insert(self, issue: FeedbackIssue) -> None: ...
    async def get(self, issue_id: UUID) -> FeedbackIssue | None: ...
    async def replace(self, issue: FeedbackIssue, expected_revision: int) -> bool: ...
    async def delete(self, issue_id: UUID, revision: int) -> bool: ...
    async def page(self, query: FeedbackQuery) -> FeedbackPage: ...
    async def add_attachment(
        self, issue: FeedbackIssue, expected_revision: int, attachment: FeedbackAttachmentRecord
    ) -> bool: ...
    async def get_attachment(self, issue_id: UUID, attachment_id: UUID) -> FeedbackAttachmentRecord | None: ...
    async def insert_token(self, record: FeedbackTokenRecord) -> None: ...
    async def find_token(self, token_hash: str) -> FeedbackTokenRecord | None: ...
    async def list_tokens(self) -> tuple[FeedbackTokenRecord, ...]: ...
    async def revoke_token(self, token_id: UUID, now: datetime) -> bool: ...


_CONFLICT: Final = FeedbackFailure(409, "feedback_revision_conflict", "记录已被修改，请重新读取后再操作")
_FORBIDDEN: Final = FeedbackFailure(403, "feedback_forbidden", "没有此反馈操作的权限")
_MISSING: Final = FeedbackFailure(404, "feedback_not_found", "反馈不存在或已删除")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _public_token(record: FeedbackTokenRecord) -> FeedbackTokenView:
    return FeedbackTokenView.model_validate(record.model_dump(exclude={"token_hash"}))


def _transition(
    issue: FeedbackIssue, request: FeedbackMutation, actor: FeedbackActor, now: datetime
) -> FeedbackIssue | FeedbackFailure:
    if request.expected_revision != issue.revision:
        return _CONFLICT
    if not actor.manager and request.action != "claim" and issue.assigned_to != actor.id:
        return _FORBIDDEN
    targets: Final = {
        "claim": ("pending", "in_progress"),
        "submit_fix": ("in_progress", "pending_verification"),
        "resolve": ("pending_verification", "resolved"),
        "release": ("in_progress", "pending"),
        "note": ("in_progress", "in_progress"),
    }
    if request.action == "reopen":
        if issue.status == "pending":
            return FeedbackFailure(409, "feedback_invalid_transition", "反馈已经处于待处理状态")
    elif issue.status != targets[request.action][0]:
        return FeedbackFailure(409, "feedback_invalid_transition", "当前状态不允许此操作")
    if request.action == "submit_fix" and (
        not request.summary or not request.verification or re.fullmatch(r"[a-fA-F0-9]{7,64}", request.commit) is None
    ):
        return FeedbackFailure(422, "feedback_evidence_required", "提交修复需要说明、提交号和验证结果")
    if request.action == "resolve" and (not request.verification or not request.deployed_version):
        return FeedbackFailure(422, "feedback_runtime_evidence_required", "确认解决需要实际运行版本和复测结果")
    if request.action == "note" and not request.summary:
        return FeedbackFailure(422, "feedback_note_required", "处理记录不能为空")
    event: Final = FeedbackEvent(
        at=now,
        actor=actor.id,
        action=request.action,
        summary=request.summary,
        commit=request.commit,
        verification=request.verification,
        deployed_version=request.deployed_version,
    )
    reset: Final = request.action in ("release", "reopen")
    return FeedbackIssue.model_validate(
        {
            **issue.model_dump(),
            "revision": issue.revision + 1,
            "status": "pending" if request.action == "reopen" else targets[request.action][1],
            "updated_at": now,
            "assigned_to": None if reset else actor.id if request.action == "claim" else issue.assigned_to,
            "resolution": "" if reset else request.summary if request.action == "submit_fix" else issue.resolution,
            "commit": "" if reset else request.commit if request.action == "submit_fix" else issue.commit,
            "verification": ""
            if reset
            else request.verification
            if request.action in ("submit_fix", "resolve")
            else issue.verification,
            "deployed_version": ""
            if reset
            else request.deployed_version
            if request.action == "resolve"
            else issue.deployed_version,
            "events": (*issue.events, event),
        }
    )


class FeedbackService:
    def __init__(self, repository: FeedbackRepository, *, clock: Callable[[], datetime] = _now) -> None:
        self._repository: Final = repository
        self._clock: Final = clock

    async def page(self, query: FeedbackQuery) -> FeedbackPage:
        return await self._repository.page(query)

    async def get(self, issue_id: UUID) -> FeedbackIssue | FeedbackFailure:
        return await self._repository.get(issue_id) or _MISSING

    async def add_attachment(
        self,
        issue_id: UUID,
        expected_revision: int,
        filename: str,
        content: bytes,
        actor: FeedbackActor,
    ) -> FeedbackIssue | FeedbackFailure:
        if not actor.manager:
            return _FORBIDDEN
        issue: Final = await self._repository.get(issue_id)
        if issue is None:
            return _MISSING
        if issue.revision != expected_revision:
            return _CONFLICT
        attachment: Final = create_attachment(issue_id, filename, content)
        if isinstance(attachment, FeedbackAttachmentValidationFailure):
            return FeedbackFailure(attachment.status, attachment.code, attachment.message)
        now: Final = self._clock()
        metadata: Final = FeedbackAttachmentInfo.model_validate(attachment.model_dump(exclude={"issue_id", "content"}))
        updated: Final = FeedbackIssue.model_validate(
            {
                **issue.model_dump(),
                "revision": issue.revision + 1,
                "updated_at": now,
                "attachments": (*issue.attachments, metadata),
                "events": (
                    *issue.events,
                    FeedbackEvent(at=now, actor=actor.id, action="attachment", summary=metadata.filename),
                ),
            }
        )
        return updated if await self._repository.add_attachment(updated, issue.revision, attachment) else _CONFLICT

    async def get_attachment(
        self,
        issue_id: UUID,
        attachment_id: UUID,
    ) -> FeedbackAttachmentRecord | FeedbackFailure:
        attachment: Final = await self._repository.get_attachment(issue_id, attachment_id)
        return _MISSING if attachment is None else attachment

    async def create(self, request: FeedbackCreate, actor: FeedbackActor) -> FeedbackIssue | FeedbackFailure:
        if not actor.manager:
            return _FORBIDDEN
        now: Final = self._clock()
        issue: Final = FeedbackIssue(
            title=request.title,
            description=request.description,
            steps=request.steps,
            module=request.module,
            page_path=request.page_path,
            discovered_version=request.discovered_version,
            priority=request.priority,
            id=uuid4(),
            revision=1,
            created_at=now,
            updated_at=now,
            events=(FeedbackEvent(at=now, actor=actor.id, action="create"),),
        )
        await self._repository.insert(issue)
        return issue

    async def edit(
        self, issue_id: UUID, request: FeedbackEdit, actor: FeedbackActor
    ) -> FeedbackIssue | FeedbackFailure:
        if not actor.manager:
            return _FORBIDDEN
        issue: Final = await self._repository.get(issue_id)
        if issue is None:
            return _MISSING
        if issue.revision != request.expected_revision:
            return _CONFLICT
        now: Final = self._clock()
        updated: Final = FeedbackIssue.model_validate(
            {
                **issue.model_dump(),
                **request.model_dump(exclude={"expected_revision"}),
                "revision": issue.revision + 1,
                "updated_at": now,
                "events": (*issue.events, FeedbackEvent(at=now, actor=actor.id, action="edit")),
            }
        )
        return updated if await self._repository.replace(updated, issue.revision) else _CONFLICT

    async def act(
        self, issue_id: UUID, request: FeedbackMutation, actor: FeedbackActor
    ) -> FeedbackIssue | FeedbackFailure:
        issue: Final = await self._repository.get(issue_id)
        if issue is None:
            return _MISSING
        updated: Final = _transition(issue, request, actor, self._clock())
        if isinstance(updated, FeedbackFailure):
            return updated
        return updated if await self._repository.replace(updated, issue.revision) else _CONFLICT

    async def delete(self, issue_id: UUID, revision: int, actor: FeedbackActor) -> FeedbackFailure | None:
        if not actor.manager:
            return _FORBIDDEN
        if await self._repository.get(issue_id) is None:
            return _MISSING
        return None if await self._repository.delete(issue_id, revision) else _CONFLICT

    async def issue_token(self, request: FeedbackTokenCreate) -> FeedbackTokenIssue:
        now: Final = self._clock()
        token: Final = "apfb_" + secrets.token_urlsafe(32)
        record: Final = FeedbackTokenRecord(
            id=uuid4(),
            name=request.name,
            created_at=now,
            expires_at=now + timedelta(days=request.expires_in_days),
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
        )
        await self._repository.insert_token(record)
        return FeedbackTokenIssue(record=_public_token(record), token=token)

    async def list_tokens(self) -> tuple[FeedbackTokenView, ...]:
        return tuple(_public_token(record) for record in await self._repository.list_tokens())

    async def revoke_token(self, token_id: UUID) -> FeedbackFailure | None:
        if await self._repository.revoke_token(token_id, self._clock()):
            return None
        return FeedbackFailure(404, "feedback_token_not_found", "反馈凭证不存在")

    async def authenticate(self, token: str) -> FeedbackActor | FeedbackFailure:
        record: Final = await self._repository.find_token(hashlib.sha256(token.encode()).hexdigest())
        if record is None or record.revoked_at is not None or record.expires_at <= self._clock():
            return FeedbackFailure(401, "feedback_token_invalid", "反馈凭证无效、已撤销或已过期")
        return FeedbackActor("agent:" + str(record.id), False)
