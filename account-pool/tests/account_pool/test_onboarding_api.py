"""检查新接口的权限、凭据防回显及代理协议一致性。"""

import httpx
from account_pool.api import create_router
from fastapi import FastAPI
from test_onboarding_service import oauth_request, setup_service


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
    from account_pool import onboarding_models as manager

    from litellm.proxy.management_endpoints import account_pool_onboarding_models as proxy

    for name in (
        "OnboardingSupplierOption",
        "OnboardingImport",
        "OnboardingImportResult",
        "OnboardingItem",
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
