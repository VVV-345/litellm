"""验证反馈状态、证据、认领所有权和凭证边界，不调用外部服务。"""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError


class MemoryRepository:
    def __init__(self):
        self.issues = {}
        self.tokens = {}
        self.attachments = {}
        self.conflict = False

    async def insert(self, issue):
        self.issues[issue.id] = issue

    async def get(self, issue_id):
        return self.issues.get(issue_id)

    async def replace(self, issue, expected_revision):
        if self.conflict or self.issues[issue.id].revision != expected_revision:
            return False
        self.issues[issue.id] = issue
        return True

    async def delete(self, issue_id, revision):
        if issue_id not in self.issues or self.issues[issue_id].revision != revision:
            return False
        del self.issues[issue_id]
        return True

    async def page(self, query):
        from account_pool.feedback_models import FeedbackPage, FeedbackStats

        all_items = tuple(self.issues.values())
        items = tuple(
            i
            for i in all_items
            if (
                query.status == "all"
                or (query.status == "unresolved" and i.status != "resolved")
                or i.status == query.status
            )
            and query.q.casefold() in (i.title + i.description).casefold()
        )
        return FeedbackPage(
            items=items[query.offset : query.offset + query.limit],
            total=len(items),
            stats=FeedbackStats(
                **{
                    status: sum(i.status == status for i in all_items)
                    for status in ("pending", "in_progress", "pending_verification", "resolved")
                }
            ),
        )

    async def insert_token(self, record):
        self.tokens[record.id] = record

    async def find_token(self, token_hash):
        return next((r for r in self.tokens.values() if r.token_hash == token_hash), None)

    async def list_tokens(self):
        return tuple(self.tokens.values())

    async def revoke_token(self, token_id, now):
        if token_id not in self.tokens:
            return False
        self.tokens[token_id] = self.tokens[token_id].model_copy(update={"revoked_at": now})
        return True

    async def add_attachment(self, issue, expected_revision, attachment):
        if self.conflict or self.issues[issue.id].revision != expected_revision:
            return False
        self.issues[issue.id] = issue
        self.attachments[attachment.id] = attachment
        return True

    async def get_attachment(self, issue_id, attachment_id):
        attachment = self.attachments.get(attachment_id)
        return attachment if attachment is not None and attachment.issue_id == issue_id else None


def setup_service():
    from account_pool.feedback import FeedbackActor, FeedbackService

    repo = MemoryRepository()
    clock = [datetime(2026, 10, 20, tzinfo=timezone.utc)]
    return repo, FeedbackService(repo, clock=lambda: clock[0]), FeedbackActor("admin:test", True), clock


async def make_issue(service, actor):
    from account_pool.feedback_models import FeedbackCreate

    return await service.create(
        FeedbackCreate(title=" 保存后不更新 ", description="页面仍显示旧值", module="settings"), actor
    )


def test_feedback_input_is_strict_and_never_accepts_url_secrets():
    from account_pool.feedback_models import FeedbackCreate

    for fields in (
        {"title": " ", "description": "x"},
        {"title": "a", "description": " "},
        {"title": "a", "description": "b", "page_path": "/ui?token=secret"},
        {"title": "a", "description": "b", "page_path": "https://host/ui"},
        {"title": "a", "description": "b", "status": "resolved"},
    ):
        with pytest.raises(ValidationError):
            FeedbackCreate(**fields)
    assert FeedbackCreate(title=" 标题 ", description=" 详情 ").title == "标题"


@pytest.mark.asyncio
async def test_real_state_machine_retains_evidence_and_reopens():
    from account_pool.feedback_models import FeedbackIssue, FeedbackMutation

    repo, service, admin, _ = setup_service()
    issue = await make_issue(service, admin)
    assert issue.title == "保存后不更新" and issue.status == "pending" and issue.revision == 1
    claimed = await service.act(issue.id, FeedbackMutation(action="claim", expected_revision=1), admin)
    assert (
        isinstance(claimed, FeedbackIssue) and claimed.status == "in_progress" and claimed.assigned_to == "admin:test"
    )
    fixed = await service.act(
        issue.id,
        FeedbackMutation(
            action="submit_fix",
            expected_revision=2,
            summary="刷新缓存",
            commit="a" * 40,
            verification="pytest: 3 passed",
        ),
        admin,
    )
    assert isinstance(fixed, FeedbackIssue) and fixed.status == "pending_verification" and fixed.commit == "a" * 40
    resolved = await service.act(
        issue.id,
        FeedbackMutation(
            action="resolve",
            expected_revision=3,
            summary="已复测",
            verification="HTTP 200; 页面显示新值",
            deployed_version="v1.7.0",
        ),
        admin,
    )
    assert (
        isinstance(resolved, FeedbackIssue) and resolved.status == "resolved" and resolved.deployed_version == "v1.7.0"
    )
    reopened = await service.act(
        issue.id, FeedbackMutation(action="reopen", expected_revision=4, summary="再次复现"), admin
    )
    assert isinstance(reopened, FeedbackIssue) and reopened.status == "pending" and reopened.assigned_to is None
    assert [event.action for event in reopened.events] == ["create", "claim", "submit_fix", "resolve", "reopen"]
    assert reopened.events[2].verification == "pytest: 3 passed"


@pytest.mark.asyncio
async def test_required_fix_and_runtime_evidence_cannot_be_bypassed():
    from account_pool.feedback import FeedbackFailure
    from account_pool.feedback_models import FeedbackMutation

    repo, service, admin, _ = setup_service()
    issue = await make_issue(service, admin)
    bad = await service.act(
        issue.id,
        FeedbackMutation(action="resolve", expected_revision=1, verification="ok", deployed_version="v2"),
        admin,
    )
    assert isinstance(bad, FeedbackFailure) and bad.status == 409
    await service.act(issue.id, FeedbackMutation(action="claim", expected_revision=1), admin)
    for fields in (
        {},
        {"summary": "fix", "commit": "badsha", "verification": "ok"},
        {"summary": "fix", "commit": "a" * 40},
    ):
        bad = await service.act(issue.id, FeedbackMutation(action="submit_fix", expected_revision=2, **fields), admin)
        assert isinstance(bad, FeedbackFailure) and bad.status == 422
    assert repo.issues[issue.id].revision == 2


@pytest.mark.asyncio
async def test_cas_and_agent_ownership_prevent_overwrites():
    from account_pool.feedback import FeedbackActor, FeedbackFailure
    from account_pool.feedback_models import FeedbackMutation

    repo, service, admin, _ = setup_service()
    issue = await make_issue(service, admin)
    first = FeedbackActor("agent:one", False)
    second = FeedbackActor("agent:two", False)
    await service.act(issue.id, FeedbackMutation(action="claim", expected_revision=1), first)
    stolen = await service.act(
        issue.id, FeedbackMutation(action="note", expected_revision=2, summary="take over"), second
    )
    assert isinstance(stolen, FeedbackFailure) and stolen.status == 403
    stale = await service.act(issue.id, FeedbackMutation(action="release", expected_revision=1), first)
    assert isinstance(stale, FeedbackFailure) and stale.status == 409
    repo.conflict = True
    racing = await service.act(
        issue.id, FeedbackMutation(action="note", expected_revision=2, summary="progress"), first
    )
    assert isinstance(racing, FeedbackFailure) and racing.status == 409
    assert repo.issues[issue.id].revision == 2


@pytest.mark.asyncio
async def test_token_is_hash_only_expiring_revocable_and_not_admin():
    from account_pool.feedback import FeedbackFailure
    from account_pool.feedback_models import FeedbackTokenCreate

    repo, service, admin, clock = setup_service()
    issued = await service.issue_token(FeedbackTokenCreate(name="Claude Code", expires_in_days=1))
    stored = repo.tokens[issued.record.id]
    assert stored.token_hash != issued.token and issued.token not in repr(stored)
    actor = await service.authenticate(issued.token)
    assert not isinstance(actor, FeedbackFailure) and not actor.manager
    assert actor.id == "agent:" + str(issued.record.id)
    assert isinstance(await service.authenticate("wrong"), FeedbackFailure)
    clock[0] += timedelta(days=2)
    assert isinstance(await service.authenticate(issued.token), FeedbackFailure)
    clock[0] -= timedelta(days=2)
    await service.revoke_token(issued.record.id)
    assert isinstance(await service.authenticate(issued.token), FeedbackFailure)
    assert "token_hash" not in (await service.list_tokens())[0].model_dump()


@pytest.mark.asyncio
async def test_edit_delete_require_current_revision_and_admin():
    from account_pool.feedback import FeedbackActor, FeedbackFailure
    from account_pool.feedback_models import FeedbackEdit

    repo, service, admin, _ = setup_service()
    issue = await make_issue(service, admin)
    edit = FeedbackEdit(title="新标题", description="新详情", expected_revision=1)
    denied = await service.edit(issue.id, edit, FeedbackActor("agent:x", False))
    assert isinstance(denied, FeedbackFailure) and denied.status == 403
    changed = await service.edit(issue.id, edit, admin)
    assert changed.title == "新标题" and changed.revision == 2
    stale = await service.delete(issue.id, 1, admin)
    assert isinstance(stale, FeedbackFailure) and stale.status == 409
    assert await service.delete(issue.id, 2, admin) is None
    assert issue.id not in repo.issues


@pytest.mark.asyncio
async def test_attachment_is_validated_versioned_and_downloadable_only_for_its_issue():
    from account_pool.feedback import FeedbackFailure
    from account_pool.feedback_models import FeedbackIssue

    repo, service, admin, _ = setup_service()
    issue = await make_issue(service, admin)
    png = b"\x89PNG\r\n\x1a\n" + b"body"
    updated = await service.add_attachment(issue.id, 1, r"..\\capture.png", png, admin)
    assert isinstance(updated, FeedbackIssue)
    assert updated.revision == 2
    assert updated.attachments[0].filename == "capture.png"
    assert updated.attachments[0].content_type == "image/png"
    attachment = await service.get_attachment(issue.id, updated.attachments[0].id)
    assert attachment.content == png
    assert await service.get_attachment(issue.id, updated.attachments[0].id) == attachment
    assert isinstance(await service.get_attachment(issue.id, issue.id), FeedbackFailure)


@pytest.mark.asyncio
async def test_attachment_rejects_mismatched_signature_invalid_text_and_oversize_files():
    from account_pool.feedback import FeedbackFailure

    repo, service, admin, _ = setup_service()
    issue = await make_issue(service, admin)
    invalid = (
        ("photo.png", b"not a png"),
        ("notes.txt", b"\xff\x00"),
        ("large.log", b"a" * (10 * 1024 * 1024 + 1)),
    )
    for filename, content in invalid:
        result = await service.add_attachment(issue.id, 1, filename, content, admin)
        assert isinstance(result, FeedbackFailure)
    assert repo.issues[issue.id].revision == 1
    assert repo.attachments == {}


def test_attachment_accepts_each_approved_format():
    from uuid import uuid4

    from account_pool.feedback_attachments import create_attachment
    from account_pool.feedback_models import FeedbackAttachmentRecord

    issue_id = uuid4()
    samples = (
        ("capture.png", b"\x89PNG\r\n\x1a\nbody", "image/png"),
        ("capture.jpeg", b"\xff\xd8\xffbody", "image/jpeg"),
        ("capture.webp", b"RIFF\x00\x00\x00\x00WEBPbody", "image/webp"),
        ("report.pdf", b"%PDF-1.7 body", "application/pdf"),
        ("notes.txt", b"UTF-8 text", "text/plain"),
        ("trace.log", b"UTF-8 log", "text/plain"),
    )
    for filename, content, expected_type in samples:
        attachment = create_attachment(issue_id, filename, content)
        assert isinstance(attachment, FeedbackAttachmentRecord)
        assert attachment.content_type == expected_type


@pytest.mark.asyncio
async def test_machine_actor_cannot_upload_feedback_attachments():
    from account_pool.feedback import FeedbackActor, FeedbackFailure

    repo, service, admin, _ = setup_service()
    issue = await make_issue(service, admin)
    result = await service.add_attachment(
        issue.id,
        1,
        "image.png",
        b"\x89PNG\r\n\x1a\nbody",
        FeedbackActor("agent:test", False),
    )
    assert isinstance(result, FeedbackFailure) and result.status == 403
    assert repo.issues[issue.id].revision == 1
