"""检查新接口的权限、凭据防回显及代理协议一致性。"""

import httpx
import pytest
from account_pool.api import create_router
from account_pool.domain import EnvironmentStatus, ProxyMode
from fastapi import FastAPI
from test_account_pool import _record
from test_onboarding_service import oauth_request, setup_service


@pytest.mark.parametrize("filename", ["account.json", "account.txt", "account.TXT"])
@pytest.mark.parametrize("format", ["cpa", "sub2api"])
async def test_single_json_content_upload_normalizes_downstream_filename(tmp_path, filename, format):
    import json
    from email import policy
    from email.parser import BytesParser

    from account_pool.channels.cliproxyapi.client import HttpCLIProxyClient
    from account_pool.secrets import EnvironmentSecretDeriver
    from test_account_pool import FakeCLIProxy
    from test_onboarding_service import sub2api_fixture

    uploaded = []

    def upstream(request):
        if request.method == "GET":
            return httpx.Response(200, json={"files": []})
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.content
        )
        part = next(message.iter_parts())
        if not part.get_filename().lower().endswith(".json"):
            return httpx.Response(400, json={"error": "file must be .json"})
        uploaded.append((part.get_filename(), part.get_content_type(), json.loads(part.get_payload(decode=True))))
        return httpx.Response(200, json={"status": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as transport:
        proxy = HttpCLIProxyClient(EnvironmentSecretDeriver("s" * 32), client=transport)

        class UploadClient(FakeCLIProxy):
            async def upload_auth_file(self, record, name, content, content_type):
                await proxy.upload_auth_file(record, name, content, content_type)
                await super().upload_auth_file(record, name, content, content_type)

        onboarding, _, environments, _, _, service = setup_service(tmp_path, UploadClient())
        record = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None)
        await environments.save(record)
        app = FastAPI()
        app.include_router(create_router(service, "manager-test", onboarding_service=onboarding))
        content = sub2api_fixture() if format == "sub2api" else {"type": "codex", "refresh_token": "fixture-refresh"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/auth-files", headers={"Authorization": "Bearer manager-test"},
                data={"card_id": str(record.id)}, files={"file": (filename, json.dumps(content), "text/plain")},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "ready"
        assert (await environments.get(record.id)).auth_file_name == "account.json"
        assert uploaded[0][0:2] == ("account.json", "application/json")
        assert uploaded[0][2]["refresh_token"] == "fixture-refresh"
        assert uploaded[0][2]["type"] == "codex" and "credentials" not in uploaded[0][2]
        assert "fixture-refresh" not in response.text


@pytest.mark.parametrize("prefix", ["", "卡密导出\n\n", "\ufeff \r\n 卡密导出 \r\n\r\n"])
async def test_single_two_factor_upload_reuses_card_and_keeps_secrets_out_of_response(tmp_path, prefix):
    service, tasks, environments, _, cli, environment_service = setup_service(tmp_path)
    record = _record(status=EnvironmentStatus.AWAITING_AUTHORIZATION, auth_file_name=None).model_copy(
        update={"proxy_mode": ProxyMode.PROFILE, "proxy_profile_id": "onboarding-proxy"}
    )
    await environments.save(record)
    app = FastAPI()
    app.include_router(create_router(environment_service, "manager-test", onboarding_service=service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        headers = {"Authorization": "Bearer manager-test"}
        for _ in range(2):
            response = await client.post(
                "/api/auth-files",
                headers=headers,
                data={"card_id": str(record.id)},
                files={
                    "file": (
                        "account.txt",
                        (prefix + "fixture@example.test----fixture-password----JBSWY3DPEHPK3PXP").encode(),
                        "text/plain",
                    )
                },
            )
            assert response.status_code == 200
            assert response.json()["id"] == str(record.id)
            assert response.json()["status"] == "awaiting_authorization"
            assert "fixture-password" not in response.text and "JBSWY3DPEHPK3PXP" not in response.text
        assert len(tasks.rows) == 1 and not cli.upload_calls
        item = next(iter(tasks.rows.values())).item
        reveal = await client.post(f"/api/onboarding/items/{item.id}/secrets", headers=headers)
        assert reveal.headers["cache-control"] == "no-store"
        assert reveal.json()["totp_secret"] == "JBSWY3DPEHPK3PXP"


async def test_two_factor_upload_does_not_replace_working_token_or_accept_multiple_accounts(tmp_path):
    service, _, environments, _, cli, environment_service = setup_service(tmp_path)
    record = (await environments.list())[0]
    app = FastAPI()
    app.include_router(create_router(environment_service, "manager-test", onboarding_service=service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/auth-files",
            headers={"Authorization": "Bearer manager-test"},
            data={"card_id": str(record.id), "replace": "true"},
            files={"file": ("account.txt", b"fixture@example.test----password----JBSWY3DPEHPK3PXP", "text/plain")},
        )
    assert response.status_code == 409
    assert (await environments.get(record.id)).auth_file_name == record.auth_file_name
    assert not cli.upload_calls


async def test_manager_requires_auth_and_never_echoes_passwords_in_validation_errors(tmp_path):
    service, _, environments, _, _, environment_service = setup_service(tmp_path)
    app = FastAPI()
    app.include_router(create_router(environment_service, "manager-test", onboarding_service=service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.get("/api/onboarding/items")
        assert denied.status_code == 401
        headers = {"Authorization": "Bearer manager-test"}
        body = oauth_request().model_dump(mode="json")
        body["job_id"] = "invalid-secret-shaped-id"
        bad = await client.post("/api/onboarding/imports", headers=headers, json=body)
        assert bad.status_code == 422
        assert "mail-secret" not in bad.text
        assert "supplier-secret" not in bad.text
        assert "invalid-secret-shaped-id" not in bad.text
        good = await client.post(
            "/api/onboarding/imports", headers=headers, json=oauth_request().model_dump(mode="json")
        )
        assert good.status_code == 202
        assert "mail-secret" not in good.text
        item_id = good.json()["items"][0]["id"]
        reveal = await client.post(f"/api/onboarding/items/{item_id}/secrets", headers=headers)
        assert reveal.headers["cache-control"] == "no-store"
        assert reveal.json()["mailbox_password"] == "mail-secret"


def test_manager_and_proxy_onboarding_contracts_match():
    from account_pool.onboarding import models as manager

    from litellm.proxy.management_endpoints import account_pool_onboarding_models as proxy

    for name in (
        "OnboardingSupplierOption",
        "OnboardingImport",
        "OnboardingImportResult",
        "OnboardingItem",
        "OnboardingCleanup",
        "OnboardingCleanupResult",
        "OnboardingAction",
        "OnboardingSecrets",
        "OnboardingTarget",
        "OnboardingTargetView",
    ):
        assert getattr(manager, name).model_json_schema() == getattr(proxy, name).model_json_schema()


async def test_supplier_catalog_matches_registered_capabilities(tmp_path):
    from account_pool.provider_families import PROVIDER_FAMILIES

    service, _, _, _, _, environment_service = setup_service(tmp_path)
    app = FastAPI()
    app.include_router(create_router(environment_service, "manager-test", onboarding_service=service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/onboarding/suppliers", headers={"Authorization": "Bearer manager-test"})
        assert response.status_code == 200
        items = {item["supplier"]: item for item in response.json()}
        assert set(items) == {family.supplier.value for family in PROVIDER_FAMILIES}
        assert items["openai_codex"]["oauth"] and items["kimi"]["oauth"]
        assert not items["vertex"]["oauth"] and not items["gemini"]["oauth"]


async def test_manager_cleanup_requires_auth_and_rejects_naive_cutoffs(tmp_path):
    service, _, _, _, _, environment_service = setup_service(tmp_path)
    app = FastAPI()
    app.include_router(create_router(environment_service, "manager-test", onboarding_service=service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post("/api/onboarding/items/cleanup", json={})
        assert denied.status_code == 401
        headers = {"Authorization": "Bearer manager-test"}
        bad = await client.post(
            "/api/onboarding/items/cleanup", headers=headers, json={"completed_before": "2026-10-01T00:00:00"}
        )
        assert bad.status_code == 422
        good = await client.post("/api/onboarding/items/cleanup", headers=headers, json={"source": "auth_file"})
        assert good.status_code == 200 and good.json() == {"cleared": 0}


@pytest.mark.parametrize("selected,actual,expected", [
    ("cpa", "cpa", "valid"), ("sub2api", "sub2api", "valid"),
    ("cpa", "sub2api", "invalid"), ("sub2api", "cpa", "invalid"),
    ("cpa", "2fa", "invalid"), ("sub2api", "2fa", "invalid"),
])
async def test_explicit_file_imports_separate_cpa_and_sub2api_from_2fa(tmp_path, selected, actual, expected):
    import json
    from uuid import uuid4

    from test_onboarding_service import sub2api_fixture

    service, tasks, _, _, _, environments = setup_service(tmp_path)
    app = FastAPI()
    app.include_router(create_router(environments, "manager-test", onboarding_service=service))
    content = {
        "cpa": json.dumps({"type": "codex", "refresh_token": "fixture-refresh"}),
        "sub2api": json.dumps(sub2api_fixture()),
        "2fa": "fixture@example.test----fixture-password----JBSWY3DPEHPK3PXP",
    }[actual]
    body = {
        "job_id": str(uuid4()), "source": "auth_file", "supplier": "openai_codex", "auth_file_format": selected,
        "entries": [{"label": "fixture.json", "content": content}],
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        headers = {"Authorization": "Bearer manager-test"}
        preview = await client.post("/api/onboarding/preview", headers=headers, json=body)
        assert preview.status_code == 200
        assert preview.json()[0]["status"] == expected
        if expected == "invalid":
            await client.post("/api/onboarding/imports", headers=headers, json=body)
            assert not tasks.rows
    assert "fixture-refresh" not in preview.text and "fixture-password" not in preview.text
