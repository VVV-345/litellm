"""验证反馈 HTTP 接口的私网鉴权、限权机器操作和不泄露错误。"""

from uuid import uuid4

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from test_feedback import setup_service


def client_for():
    from account_pool.feedback_api import create_feedback_router

    repo, service, _, _ = setup_service()

    def authorize(authorization: str = Header(default="")):
        if authorization != "Bearer manager-test":
            raise HTTPException(401)

    app = FastAPI()
    app.include_router(create_feedback_router(service, authorize))
    return TestClient(app), repo


HEADERS = {"Authorization": "Bearer manager-test", "X-Feedback-Actor": "admin:tester"}


def test_http_crud_conflict_and_agent_scope():
    client, repo = client_for()
    assert client.get("/api/feedback/issues").status_code == 401
    created = client.post("/api/feedback/issues", headers=HEADERS, json={"title": "标题", "description": "问题"})
    assert created.status_code == 200
    issue = created.json()
    issue_id = issue["id"]
    assert issue["events"][0]["actor"] == "admin:tester"
    token_response = client.post("/api/feedback/tokens", headers=HEADERS, json={"name": "test harness"})
    assert token_response.headers["cache-control"] == "no-store"
    token = token_response.json()["token"]
    token_id = token_response.json()["record"]["id"]
    assert token.startswith("apfb_")
    assert "token_hash" not in client.get("/api/feedback/tokens", headers=HEADERS).text
    command = {
        "token": token,
        "operation": "mutate",
        "issue_id": issue_id,
        "mutation": {"action": "claim", "expected_revision": 1},
    }
    claimed = client.post("/api/feedback/agent", headers=HEADERS, json=command)
    assert claimed.status_code == 200 and claimed.json()["status"] == "in_progress"
    assert claimed.json()["assigned_to"] == "agent:" + token_id
    stale = client.put(
        "/api/feedback/issues/" + issue_id,
        headers=HEADERS,
        json={"title": "overwrite", "description": "x", "expected_revision": 1},
    )
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "feedback_revision_conflict"
    fixed = client.post(
        "/api/feedback/agent",
        headers=HEADERS,
        json={
            **command,
            "mutation": {
                "action": "submit_fix",
                "expected_revision": 2,
                "summary": "fixed",
                "commit": "a" * 40,
                "verification": "3 passed",
            },
        },
    )
    assert fixed.status_code == 200 and fixed.json()["status"] == "pending_verification"
    page = client.get("/api/feedback/issues", headers=HEADERS).json()
    assert page["stats"]["pending_verification"] == 1 and page["total"] == 1
    assert client.delete("/api/feedback/tokens/" + token_id, headers=HEADERS).status_code == 204
    revoked = client.post("/api/feedback/agent", headers=HEADERS, json={"token": token, "operation": "list"})
    assert revoked.status_code == 401 and token not in revoked.text
    assert client.delete("/api/feedback/issues/" + issue_id + "?revision=3", headers=HEADERS).status_code == 204
    assert client.get("/api/feedback/issues/" + issue_id, headers=HEADERS).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"token": "TOP-SECRET", "operation": "delete"},
        {"token": "TOP-SECRET", "operation": "list", "unexpected": "TOP-SECRET"},
    ],
)
def test_invalid_agent_input_does_not_echo_secret(body):
    client, _ = client_for()
    response = client.post("/api/feedback/agent", headers=HEADERS, json=body)
    assert response.status_code == 422 and "TOP-SECRET" not in response.text


def test_machine_actions_are_bound_to_issue_and_query_is_limited():
    client, _ = client_for()
    token = client.post("/api/feedback/tokens", headers=HEADERS, json={"name": "harness"}).json()["token"]
    response = client.post("/api/feedback/agent", headers=HEADERS, json={"token": token, "operation": "mutate"})
    assert response.status_code == 422
    response = client.get("/api/feedback/issues?limit=101", headers=HEADERS)
    assert response.status_code == 422
    assert client.delete("/api/feedback/issues/" + str(uuid4()) + "?revision=0", headers=HEADERS).status_code == 422


def test_upload_checks_signature_and_machine_token_can_only_download():
    client, _ = client_for()
    issue = client.post(
        "/api/feedback/issues", headers=HEADERS, json={"title": "截图问题", "description": "上传图片"}
    ).json()
    issue_id = issue["id"]
    rejected = client.post(
        f"/api/feedback/issues/{issue_id}/attachments",
        headers=HEADERS,
        data={"expected_revision": "1"},
        files={"file": ("fake.png", b"not a png", "image/png")},
    )
    assert rejected.status_code == 422
    uploaded = client.post(
        f"/api/feedback/issues/{issue_id}/attachments",
        headers=HEADERS,
        data={"expected_revision": "1"},
        files={"file": ("..\\capture.png", b"\x89PNG\r\n\x1a\nbody", "text/plain")},
    )
    assert uploaded.status_code == 200
    saved = uploaded.json()
    assert saved["revision"] == 2
    attachment = saved["attachments"][0]
    assert attachment["filename"] == "capture.png" and attachment["content_type"] == "image/png"
    token = client.post("/api/feedback/tokens", headers=HEADERS, json={"name": "harness"}).json()["token"]
    downloaded = client.post(
        f"/api/feedback/agent/attachments/{issue_id}/{attachment['id']}",
        headers=HEADERS,
        json={"token": token},
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"\x89PNG\r\n\x1a\nbody"
    assert downloaded.headers["content-type"] == "image/png"
    assert downloaded.headers["x-content-type-options"] == "nosniff"
