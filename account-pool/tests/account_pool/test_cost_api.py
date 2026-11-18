"""验证私网台账接口鉴权、日期拒绝及存储异常脱敏。"""

from datetime import date, timedelta
from typing import Final
from uuid import UUID, uuid4

import psycopg
from account_pool.cost_api import create_cost_router
from account_pool.cost_models import PoolCostCreate, PoolCostRecord
from account_pool.cost_repository import CostFailure
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from test_account_pool import MemoryRepository
from test_quota_reset import setup_service


class UnavailableCosts:
    async def list(self) -> tuple[PoolCostRecord, ...]:
        return ()

    async def create(self, request: PoolCostCreate, actor: str) -> PoolCostRecord | CostFailure:
        raise psycopg.OperationalError("private-db-password-do-not-expose")

    async def stop_renewal(self, record_id: UUID) -> PoolCostRecord | CostFailure:
        return CostFailure(404, "费用记录不存在")

    async def void(self, record_id: UUID, reason: str, actor: str) -> PoolCostRecord | CostFailure:
        return CostFailure(404, "费用记录不存在")


def test_manager_auth_future_payment_report_and_safe_storage_error():
    _, account, _, _ = setup_service()

    def authorize(authorization: str = Header(default="")) -> None:
        if authorization != "Bearer test-private-token":
            raise HTTPException(401)

    app: Final = FastAPI()
    app.include_router(create_cost_router(UnavailableCosts(), MemoryRepository(account), authorize))
    client: Final = TestClient(app)
    query: Final = "/api/costs?starts_on=2026-01-01&ends_on=2026-01-31"
    assert client.get(query).status_code == 401
    headers: Final = {"Authorization": "Bearer test-private-token"}
    report: Final = client.get(query, headers=headers)
    assert report.status_code == 200
    assert report.headers["cache-control"] == "no-store"
    assert report.json()["paid"]["total_cents"] == 0
    payload: Final = {
        "id": str(uuid4()),
        "category": "subscription",
        "account_id": str(account.id),
        "name": "月费",
        "amount_cents": 19900,
        "paid_on": "2026-01-01",
        "starts_on": "2026-01-01",
        "ends_on": "2026-01-31",
    }
    assert client.post("/api/costs", json=payload).status_code == 401
    future: Final = client.post(
        "/api/costs", json={**payload, "paid_on": (date.today() + timedelta(days=2)).isoformat()}, headers=headers
    )
    assert future.status_code == 422
    failed: Final = client.post("/api/costs", json=payload, headers=headers)
    assert failed.status_code == 503
    assert "private-db-password" not in failed.text
    missing: Final = client.post(f"/api/costs/{uuid4()}/void", json={"reason": "误录"}, headers=headers)
    assert missing.status_code == 404
    assert client.get("/api/costs?starts_on=2026-02-01&ends_on=2026-01-01", headers=headers).status_code == 422
