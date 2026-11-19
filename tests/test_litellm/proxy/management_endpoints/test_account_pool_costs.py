"""验证成本 API 管理员边界、固定路径、审计身份和下游错误。"""

import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.management_endpoints.account_pool_costs import create_cost_router


def client_for(admin=True, response_status=200):
    seen = []

    async def manager(method, path, body):
        seen.append((method, path))
        if response_status != 200:
            return httpx.Response(response_status, json={"detail": "账号不存在"})
        value = json.loads(body)
        return httpx.Response(
            200, json={**value, "created_at": datetime.now(timezone.utc).isoformat(), "created_by": "admin:tester"}
        )

    def require_admin(user):
        if not admin:
            raise HTTPException(403)

    app = FastAPI()
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(user_id="tester")
    app.include_router(create_cost_router(manager, require_admin), prefix="/account_pool")
    return TestClient(app), seen


def payload():
    return {
        "id": str(uuid4()),
        "category": "server",
        "name": "本地服务器",
        "amount_cents": 10000,
        "paid_on": "2026-01-01",
        "starts_on": "2026-01-01",
        "ends_on": "2026-01-31",
    }


def test_all_cost_routes_require_admin():
    client, seen = client_for(admin=False)
    assert client.get("/account_pool/costs?starts_on=2026-01-01&ends_on=2026-01-31").status_code == 403
    assert client.post("/account_pool/costs", json=payload()).status_code == 403
    assert client.post(f"/account_pool/costs/{uuid4()}/void", json={"reason": "误录"}).status_code == 403
    assert client.post(f"/account_pool/costs/{uuid4()}/stop-renewal").status_code == 403
    assert client.post(f"/account_pool/costs/{uuid4()}/replace", json=payload()).status_code == 403
    assert not seen


def test_actor_cannot_be_spoofed_and_manager_error_preserved():
    client, seen = client_for()
    response = client.post("/account_pool/costs?actor=attacker", json=payload())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert seen == [("POST", "/api/costs?actor=admin%3Atester")]
    failed, _ = client_for(response_status=404)
    assert failed.post("/account_pool/costs", json=payload()).json() == {"detail": "账号不存在"}


def test_invalid_amount_and_account_scope_not_forwarded():
    client, seen = client_for()
    assert client.post("/account_pool/costs", json={**payload(), "amount_cents": 1.25}).status_code == 422
    assert client.post("/account_pool/costs", json={**payload(), "category": "subscription"}).status_code == 422
    assert not seen


def test_stop_renewal_forwards_fixed_uuid_path_and_validates_response():
    from litellm.proxy.management_endpoints.account_pool_cost_models import PoolCostRecord

    record_id = uuid4()
    seen = []

    async def manager(method, path, body):
        seen.append((method, path, body))
        record = PoolCostRecord(**{**payload(), "id": record_id},
            created_at=datetime.now(timezone.utc), created_by="admin:tester")
        return httpx.Response(200, json=record.model_dump(mode="json"))

    app = FastAPI()
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(user_id="tester")
    app.include_router(create_cost_router(manager, lambda _: None), prefix="/account_pool")
    response = TestClient(app).post(f"/account_pool/costs/{record_id}/stop-renewal")
    assert response.status_code == 200 and response.json()["auto_renew"] is False
    assert seen == [("POST", f"/api/costs/{record_id}/stop-renewal", None)]


def test_replacement_forwards_original_id_payload_and_authenticated_actor():
    client, seen = client_for()
    original_id = uuid4()
    body = payload()
    response = client.post(f"/account_pool/costs/{original_id}/replace?actor=attacker", json=body)
    assert response.status_code == 200
    assert response.json()["id"] == body["id"]
    assert seen == [("POST", f"/api/costs/{original_id}/replace?actor=admin%3Atester")]
