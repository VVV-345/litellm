"""验证延迟日志状态只更新同一事件的两个字段，不能重复计费或改写用量。"""

import os
from uuid import uuid4

import pytest
from account_pool.error_logs import ErrorLogRecord
from account_pool.gateway_contracts import LogStatusRequest
from account_pool.management_repository import PostgresErrorLogRepository, initialize_management_schema
from account_pool.repository import PostgresEnvironmentRepository
from pydantic import ValidationError


def test_log_status_contract_cannot_modify_billing_and_matches_gateway():
    from litellm.proxy.management_endpoints.account_pool_gateway_contracts import LogStatusRequest as GatewayStatus

    assert LogStatusRequest.model_json_schema() == GatewayStatus.model_json_schema()
    with pytest.raises(ValidationError):
        LogStatusRequest(lease_id=uuid4(), full_log_state="stored", spend_sync_state="synced", cost_usd=0)


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("ACCOUNT_POOL_TEST_DATABASE_URL"), reason="requires isolated PostgreSQL")
async def test_persistence_status_is_idempotent_and_preserves_one_event_and_all_billing_fields():
    url = os.environ["ACCOUNT_POOL_TEST_DATABASE_URL"]
    assert "/account_pool_test" in url
    await PostgresEnvironmentRepository(url).initialize()
    await initialize_management_schema(url)
    repository = PostgresErrorLogRepository(url)
    card = uuid4()
    event = ErrorLogRecord(
        channel="cliproxyapi",
        supplier="openai_codex",
        card_id=card,
        account_id=card,
        environment_id=card,
        operation="model_request",
        stage="response",
        message="completed",
        http_status=200,
        input_tokens=11,
        output_tokens=7,
        cost_usd=0.25,
    )
    await repository.append(event)
    before = await repository.detail(event.event_id)
    status = LogStatusRequest(lease_id=event.event_id, full_log_state="stored", spend_sync_state="synced")
    assert await repository.update_persistence(status)
    assert await repository.update_persistence(status)
    after = await repository.detail(event.event_id)
    assert before is not None and after is not None
    assert after.event.model_dump(exclude={"full_log_state", "spend_sync_state"}) == before.event.model_dump(
        exclude={"full_log_state", "spend_sync_state"}
    )
    assert after.event.full_log_state == "stored" and after.event.spend_sync_state == "synced"
    assert len(after.attempts) == 1
    assert not await repository.update_persistence(status.model_copy(update={"lease_id": uuid4()}))


@pytest.mark.asyncio
async def test_log_status_endpoint_requires_internal_authorization_and_rejects_billing_changes():
    import httpx
    from account_pool.card_keys import CardKeyService
    from account_pool.domain import EnvironmentStatus
    from account_pool.error_logs import ErrorLogService
    from account_pool.gateway_service import GatewayService, create_gateway_router
    from fastapi import FastAPI, Header, HTTPException
    from test_account_pool import MemoryRepository, _record
    from test_gateway_service import MemoryLeases, gateway
    from test_management import MemoryKeys, MemoryLogs, MemoryPolicies

    class StatusLogs(MemoryLogs):
        def __init__(self):
            super().__init__()
            self.updates = ()

        async def update_persistence(self, request):
            self.updates = (*self.updates, request)
            return len(self.updates) == 1

    async def authorize(authorization: str | None = Header(default=None)):
        if authorization != "Bearer isolated-manager-test":
            raise HTTPException(401, "Manager authorization required")

    logs = StatusLogs()
    leases = MemoryLeases()
    service = GatewayService(
        CardKeyService(MemoryKeys()),
        MemoryRepository(_record(status=EnvironmentStatus.READY)),
        MemoryPolicies(),
        leases,
        ErrorLogService(logs),
        gateway,
    )
    app = FastAPI()
    app.include_router(create_gateway_router(service, authorize))
    body = {"lease_id": str(uuid4()), "full_log_state": "stored", "spend_sync_state": "synced"}
    headers = {"Authorization": "Bearer isolated-manager-test"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://manager.test") as client:
        assert (await client.post("/api/internal/gateway/log-status", json=body)).status_code == 401
        assert logs.updates == ()
        assert (
            await client.post("/api/internal/gateway/log-status", headers=headers, json={**body, "input_tokens": 0})
        ).status_code == 422
        assert logs.updates == ()
        assert (await client.post("/api/internal/gateway/log-status", headers=headers, json=body)).status_code == 204
        assert (await client.post("/api/internal/gateway/log-status", headers=headers, json=body)).status_code == 404
    assert len(logs.updates) == 2
    assert leases.leases == {} and leases.settled_tokens == {}
