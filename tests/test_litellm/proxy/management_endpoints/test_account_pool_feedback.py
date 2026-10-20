"""验证反馈代理只开放预定机器操作，管理员鉴权与机器凭证互不替代。"""

import json
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth


def client_for(admin=False):
    from litellm.proxy.management_endpoints.account_pool_feedback import create_feedback_router

    seen = []

    async def manager(method, path, body):
        seen.append((method, path, json.loads(body) if body else None))
        if path == "/api/feedback/agent":
            command = json.loads(body)
            if command["token"] != "limited-token":
                return httpx.Response(401, json={"detail": {"code": "feedback_token_invalid", "message": "invalid"}})
            return httpx.Response(
                200,
                json={
                    "items": [],
                    "total": 0,
                    "stats": {"pending": 0, "in_progress": 0, "pending_verification": 0, "resolved": 0},
                },
            )
        if path.startswith("/api/feedback/agent/attachments/"):
            return httpx.Response(
                200,
                content=b"\x89PNG\r\n\x1a\nbody",
                headers={
                    "Content-Type": "image/png",
                    "Content-Disposition": 'attachment; filename="capture.png"',
                    "X-Content-Type-Options": "nosniff",
                },
            )
        if method == "DELETE":
            return httpx.Response(409, json={"detail": {"code": "feedback_revision_conflict", "message": "refresh"}})
        return httpx.Response(200, json=[])

    async def multipart_manager(path, fields, filename, content, content_type):
        seen.append(
            (
                "MULTIPART",
                path,
                {"fields": fields, "filename": filename, "content": content, "content_type": content_type},
            )
        )
        return httpx.Response(
            200,
            json={
                "id": str(uuid4()),
                "revision": 2,
                "title": "截图",
                "description": "反馈",
                "priority": "normal",
                "status": "pending",
                "created_at": "2026-10-20T08:00:00Z",
                "updated_at": "2026-10-20T08:00:00Z",
                "attachments": [
                    {"id": str(uuid4()), "filename": "capture.png", "content_type": "image/png", "size": 12}
                ],
            },
        )

    def require_admin(user):
        if not admin:
            raise HTTPException(403)

    app = FastAPI()
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(user_id="human")
    app.include_router(create_feedback_router(manager, require_admin, multipart_manager), prefix="/account_pool")
    return TestClient(app), seen


def test_non_admin_cannot_create_edit_delete_or_issue_machine_tokens():
    client, seen = client_for()
    for method, path, body in [
        ("POST", "/issues", {"title": "x", "description": "y"}),
        ("PUT", "/issues/" + str(uuid4()), {"title": "x", "description": "y", "expected_revision": 1}),
        ("DELETE", "/issues/" + str(uuid4()) + "?revision=1", None),
        ("POST", "/tokens", {"name": "x"}),
    ]:
        response = client.request(
            method, "/account_pool/feedback" + path, json=body, headers={"X-Feedback-Token": "limited-token"}
        )
        assert response.status_code == 403
    assert seen == []


def test_machine_reads_are_independent_of_admin_auth_and_structured():
    client, seen = client_for()
    response = client.get(
        "/account_pool/feedback/agent/issues?status=unresolved&q=%E6%A8%A1%E5%9E%8B&limit=5",
        headers={"X-Feedback-Token": "limited-token"},
    )
    assert response.status_code == 200 and response.json()["total"] == 0
    assert response.headers["cache-control"] == "no-store"
    command = seen[0][2]
    assert command["operation"] == "list" and command["query"]["q"] == "模型" and command["query"]["limit"] == 5
    assert (
        client.post(
            "/account_pool/feedback/agent/issues", headers={"X-Feedback-Token": "limited-token"}, json={"title": "x"}
        ).status_code
        == 405
    )


def test_missing_and_invalid_machine_credentials_are_not_silently_empty():
    client, seen = client_for()
    assert client.get("/account_pool/feedback/agent/issues").status_code == 401
    assert seen == []
    response = client.get("/account_pool/feedback/agent/issues", headers={"X-Feedback-Token": "wrong"})
    assert response.status_code == 401 and response.json()["detail"]["code"] == "feedback_token_invalid"
    too_long = client.get("/account_pool/feedback/agent/issues", headers={"X-Feedback-Token": "SECRET" * 100})
    assert too_long.status_code == 422 and "SECRET" not in too_long.text


def test_admin_cas_conflict_is_preserved_for_cli_and_ui():
    client, seen = client_for(admin=True)
    response = client.delete("/account_pool/feedback/issues/" + str(uuid4()) + "?revision=2")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "feedback_revision_conflict"


def test_feedback_models_match_manager_validation_boundaries():
    from account_pool.feedback_models import FeedbackCreate as ManagerCreate
    from pydantic import ValidationError

    from litellm.proxy.management_endpoints.account_pool_feedback_models import FeedbackCreate as ProxyCreate

    for contract in (ManagerCreate, ProxyCreate):
        assert contract(title=" 标题 ", description=" 描述 ").title == "标题"
        with pytest.raises(ValidationError):
            contract(title="x", description="y", page_path="/ui?token=secret")


def test_admin_upload_uses_multipart_and_machine_download_preserves_binary_response():
    client, seen = client_for(admin=True)
    issue_id = str(uuid4())
    uploaded = client.post(
        f"/account_pool/feedback/issues/{issue_id}/attachments",
        data={"expected_revision": "1"},
        files={"file": ("capture.png", b"\x89PNG\r\n\x1a\nbody", "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["attachments"][0]["filename"] == "capture.png"
    multipart = seen[-1]
    assert multipart[0] == "MULTIPART"
    assert multipart[2]["content"] == b"\x89PNG\r\n\x1a\nbody"
    assert multipart[2]["content_type"] == "text/plain"
    downloaded = client.get(
        f"/account_pool/feedback/agent/issues/{issue_id}/attachments/{uuid4()}",
        headers={"X-Feedback-Token": "limited-token"},
    )
    assert downloaded.status_code == 200 and downloaded.content == b"\x89PNG\r\n\x1a\nbody"
    assert downloaded.headers["content-type"] == "image/png"
    assert downloaded.headers["content-disposition"] == 'attachment; filename="capture.png"'
    body = seen[-1][2]
    assert body["token"] == "limited-token"
