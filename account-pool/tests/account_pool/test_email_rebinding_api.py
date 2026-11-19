"""用真实 ASGI 路由验证管理员鉴权、显式确认、脱敏与网页脚本发布。"""

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from account_pool.email_rebinding.api import create_rebinding_router
from account_pool.oauth_automation.login_scripts import LoginScriptStore
from fastapi import FastAPI, Header, HTTPException
from test_email_rebinding import bindings as bindings


@pytest.mark.asyncio
async def test_manager_import_confirm_and_script_upload_require_admin_and_never_echo_secrets(bindings, tmp_path):
    service, card, _ = bindings
    runner = SimpleNamespace(
        service=service, scripts=LoginScriptStore(tmp_path / "script.json", "server-only-key-" * 3)
    )

    async def authorize(authorization: str = Header(default="")):
        if authorization != "Bearer fixture-admin":
            raise HTTPException(401, "unauthorized")

    app = FastAPI()
    app.include_router(create_rebinding_router(runner, authorize))
    headers = {"Authorization": "Bearer fixture-admin"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://manager.test") as client:
        assert (await client.get("/api/email-rebinding")).status_code == 401
        secret = "new@outlook.com----private-mail-password----client----private-mail-rt"
        upload = await client.post("/api/email-rebinding/mailboxes", headers=headers, json={"content": secret})
        assert upload.status_code == 200 and upload.headers["cache-control"] == "no-store"
        assert "private-mail" not in upload.text
        proposed = await client.post(
            "/api/email-rebinding/jobs", headers=headers, json={"id": str(uuid4()), "environment_id": str(card.id)}
        )
        assert proposed.status_code == 201
        job = proposed.json()
        url = f"/api/email-rebinding/jobs/{job['id']}/confirm"
        for invalid in ({"version": 0}, {"version": 0, "confirmed": 1}, {"version": 0, "confirmed": False}):
            rejected = await client.post(url, headers=headers, json=invalid)
            assert rejected.status_code == 422 and "input" not in rejected.text
        assert (await service.automation.environments.get(card.id)).oauth_automation_operation is None
        result = await client.post(url, headers=headers, json={"version": job["version"], "confirmed": True})
        assert result.status_code == 202 and result.json()["stage"] == "draining"
        exported = await client.get("/api/email-rebinding/export", headers=headers)
        assert exported.status_code == 200 and "reserved" in exported.text and "private-mail" not in exported.text
        source = "export async function rebindEmail(){return {status:'verification_required'}}"
        import base64

        package = {
            "entrypoint": "rebind.mjs",
            "files": [{"path": "rebind.mjs", "data": base64.b64encode(source.encode()).decode()}],
        }
        uploaded = await client.put("/api/email-rebinding/script", headers=headers, json={"package": package})
        assert uploaded.status_code == 200 and uploaded.json()["active"]["entrypoint"] == "rebind.mjs"
        assert "server-only-key-" * 3 not in uploaded.text and "export async" not in uploaded.text
        malicious = {"entrypoint": "../escape.mjs", "files": [{"path": "../escape.mjs", "data": "eA=="}]}
        rejected = await client.put("/api/email-rebinding/script", headers=headers, json={"package": malicious})
        assert rejected.status_code == 422 and "eA==" not in rejected.text
        assert (await client.get("/api/email-rebinding/script", headers=headers)).json()["active"] == uploaded.json()[
            "active"
        ]
