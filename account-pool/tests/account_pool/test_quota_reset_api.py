"""测试重置接口的确认、选卡约束及公开响应脱敏，不发送供应商请求。"""

import httpx
import pytest
from account_pool.quota_reset_api import create_quota_reset_router
from fastapi import FastAPI, HTTPException
from test_quota_reset import setup_service


@pytest.mark.asyncio
async def test_reset_requires_confirmation_and_does_not_expose_credential_fingerprints():
    service, record, _, upstream = setup_service()
    app = FastAPI()
    app.include_router(create_quota_reset_router(service, lambda: None))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
        root = f"/api/environments/{record.id}/quota-reset"
        prepared = await client.post(root + "/prepare", json={})
        assert prepared.status_code == 200
        assert "credential_fingerprints" not in prepared.json()
        request_id = prepared.json()["request_id"]
        rejected = await client.post(root + "/consume", json={"request_id": request_id, "confirmed": False})
        assert rejected.status_code == 422
        assert upstream.calls == []
        consumed = await client.post(root + "/consume", json={"request_id": request_id, "confirmed": True})
        assert consumed.json()["status"] == "reset"
        assert consumed.json()["verification"] == "not_run"


@pytest.mark.asyncio
async def test_all_reset_routes_require_manager_authorization():
    service, record, _, upstream = setup_service()

    def deny():
        raise HTTPException(401, "unauthorized")

    app = FastAPI()
    app.include_router(create_quota_reset_router(service, deny))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
        response = await client.get(f"/api/environments/{record.id}/quota-reset")
        assert response.status_code == 401
    assert upstream.calls == []


@pytest.mark.asyncio
async def test_saved_redemption_remains_visible_when_provider_query_fails():
    service, record, _, upstream = setup_service()
    operation = await service.prepare(record.id, None)
    upstream.timeout = True
    await service.consume(record.id, operation.request_id)

    async def failed_query(_record):
        raise ValueError("fixture upstream unavailable")

    upstream.observe = failed_query
    app = FastAPI()
    app.include_router(create_quota_reset_router(service, lambda: None))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
        response = await client.get(f"/api/environments/{record.id}/quota-reset")
    assert response.status_code == 200
    assert response.json()["operation"]["request_id"] == str(operation.request_id)
    assert response.json()["operation"]["status"] == "uncertain"
    assert response.json()["observation"] is None
