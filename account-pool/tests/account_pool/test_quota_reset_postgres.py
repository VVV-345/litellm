"""仅在显式测试数据库中验证跨实例兑换锁及恢复事务，不连接生产服务。"""

import asyncio
import os
from datetime import timedelta
from typing import Final
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from account_pool.credential_ownership import CredentialOwnership
from account_pool.domain import (
    EnvironmentStatus,
    ModelCooldown,
    ModelQuotaSnapshot,
    QuotaSnapshot,
    QuotaWindow,
    utc_now,
)
from account_pool.gateway_repository import PostgresLeaseRepository
from account_pool.quota_reset import QuotaResetService
from account_pool.quota_reset_models import ResetReply
from account_pool.quota_reset_repository import PostgresResetStore
from account_pool.repository import PostgresEnvironmentRepository, database_connection
from psycopg import sql
from test_quota_reset import Upstream, setup_service


@pytest_asyncio.fixture
async def database():
    url = os.environ.get("ACCOUNT_POOL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set ACCOUNT_POOL_TEST_DATABASE_URL to an isolated PostgreSQL test database")
    schema = "quota_reset_" + uuid4().hex
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as connection:
        await connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        scoped = psycopg.conninfo.make_conninfo(url, options=f"-c search_path={schema}")
        try:
            repo = PostgresEnvironmentRepository(scoped)
            store = PostgresResetStore(scoped)
            await repo.initialize()
            await store.initialize()
            await PostgresLeaseRepository(scoped).initialize()
            yield scoped, repo, store
        finally:
            await connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.asyncio
async def test_postgres_two_instances_share_one_redemption_and_restart_identity(database):
    url, repo, store = database
    _, record, _, upstream = setup_service()
    await repo.save(record)
    first = QuotaResetService(store, repo, upstream, CredentialOwnership(url))
    second = QuotaResetService(
        PostgresResetStore(url), PostgresEnvironmentRepository(url), upstream, CredentialOwnership(url)
    )
    left, right = await asyncio.gather(first.prepare(record.id, None), second.prepare(record.id, None))
    assert left.request_id == right.request_id
    upstream.timeout = True
    result = await first.consume(record.id, left.request_id)
    assert result.status == "uncertain"
    assert (await second.prepare(record.id, left.request_id)).request_id == left.request_id
    upstream.timeout = False
    upstream.reply = ResetReply(code="already_redeemed", windows_reset=0)
    await asyncio.gather(first.consume(record.id, left.request_id), second.consume(record.id, left.request_id))
    assert len(upstream.calls) == 1
    assert len({item[1] for item in upstream.calls}) == 1
    assert (await store.get(record.id, left.request_id)).status == "uncertain"
    assert len(upstream.calls) == 1
    assert await store.get(uuid4(), left.request_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["none", "changed", "lease", "runtime"])
async def test_postgres_verified_restore_preserves_concurrent_or_independent_blocks(database, blocker):
    url, repo, _ = database
    _, record, _, _ = setup_service()
    other = ModelCooldown(model="other", reason="consecutive_timeouts", retry_at=utc_now() + timedelta(hours=1))
    selected = ModelCooldown(model="model-a", reason="rate_limit", retry_at=utc_now() + timedelta(hours=1))
    sibling = ModelCooldown(model="model-b", reason="rate_limit", retry_at=utc_now() + timedelta(hours=1))
    before = record.model_copy(
        update={
            "status": EnvironmentStatus.COOLING_DOWN,
            "automatic_cooldown": True,
            "last_error": "上游认证失效，请重新认证",
            "model_cooldowns": (selected, sibling, other),
        }
    )
    await repo.save(before)
    if blocker == "changed":
        await repo.save(before.model_copy(update={"manual_cooldown": True}))
    async with database_connection(url) as connection:
        if blocker == "lease":
            await connection.execute(
                "INSERT INTO account_pool_leases VALUES (%s, %s, now() + interval '1 hour', '{}'::jsonb)",
                (uuid4(), record.id),
            )
        if blocker == "runtime":
            await connection.execute(
                "INSERT INTO account_pool_runtime_cooldown VALUES (%s, now() + interval '1 hour')",
                (record.id,),
            )
    current = await repo.get(record.id)
    fresh = QuotaSnapshot(
        observed_at=utc_now(),
        windows=(QuotaWindow(name="5h", remaining_percent=99, used_percent=1, window_minutes=300),),
    )
    model_quotas: Final = (ModelQuotaSnapshot(model="model-a", quota=fresh),)
    restored = await repo.restore_verified(before, "model-a", fresh, model_quotas)
    after = await repo.get(record.id)
    assert restored == (blocker == "none")
    if restored:
        assert after.status is EnvironmentStatus.READY
        assert after.model_cooldowns == (sibling, other)
        assert after.model_quotas == model_quotas
        assert after.credential_fingerprints == before.credential_fingerprints
        assert after.quota.windows == fresh.windows
        assert after.quota.observed_at == fresh.observed_at
    else:
        assert after == current


@pytest.mark.asyncio
async def test_postgres_runtime_success_does_not_hide_manager_commit_refusal(database):
    url, repo, store = database
    _, record, _, _ = setup_service()
    await repo.save(record)
    async with database_connection(url) as connection:
        await connection.execute(
            "INSERT INTO account_pool_runtime_cooldown VALUES (%s, now() + interval '1 hour')",
            (record.id,),
        )
    upstream = Upstream()
    service = QuotaResetService(store, repo, upstream, CredentialOwnership(url))
    result = await service.verify(record.id, "model-a", True)
    assert result.passed and not result.restored
    assert result.reason == "state_changed"
    assert await repo.get(record.id) == record


@pytest.mark.asyncio
async def test_ordinary_call_proof_survives_restart_and_cannot_cross_verifications(database):
    from account_pool.gateway_contracts import FinishRequest, Lease
    from account_pool.quota_reset_models import ResetVerification, ResetVerificationRecord

    url, repo, store = database
    _, record, _, _ = setup_service()
    await repo.save(record)
    verified = ResetVerificationRecord(model="model-a", result=ResetVerification(passed=True, restored=True, reason="restored"))
    await store.save_verification(record.id, verified)
    lease = Lease(lease_id=uuid4(), card_id=uuid4(), account_id=record.id, key_id=uuid4(), request_id=uuid4(),
                  model="model-a", channel=record.channel, supplier=record.supplier, started_at=utc_now())
    request = FinishRequest(lease_id=lease.lease_id, http_status=200, message="Request completed",
                            endpoint="/v1/responses", response_completed=True, stage="response")
    await store.confirm_api_call(lease, request)
    saved = await PostgresResetStore(url).latest_api_call(record.id)
    assert saved.request_id == lease.request_id and saved.model == lease.model
    assert saved.verification_at == verified.observed_at
    assert await store.latest_api_call(lease.card_id) is None
    assert await store.latest(record.id) is None
    assert await repo.get(record.id) == record
    await store.save_verification(record.id, verified.model_copy(update={"observed_at": utc_now()}))
    await store.confirm_api_call(lease, request)
    assert await store.latest_api_call(record.id) is None
    newer = lease.model_copy(update={"started_at": utc_now(), "model": "model-b"})
    await store.confirm_api_call(newer, request)
    assert await store.latest_api_call(record.id) is None
    from psycopg.types.json import Jsonb

    # 旧版只写原来的两列，新增证据列不能破坏回退后的写入。
    async with database_connection(url) as connection:
        await connection.execute(
            "INSERT INTO account_pool_quota_verifications VALUES (%s, %s) "
            "ON CONFLICT (environment_id) DO UPDATE SET payload = EXCLUDED.payload",
            (record.id, Jsonb(verified.model_dump(mode="json"))),
        )
    await store.confirm_api_call(lease.model_copy(update={"started_at": utc_now()}), request)
    assert (await store.latest_verification(record.id)) == verified
    assert (await store.latest_api_call(record.id)).request_id == lease.request_id

@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint,stream", [("/v1/responses", False), ("/v1/responses", True), ("/v1/chat/completions", False), ("/v1/chat/completions", True)])
async def test_ordinary_api_through_manager_persists_call_proof(database, endpoint, stream):
    import json

    import httpx
    from account_pool.card_keys import CardKeyService
    from account_pool.error_logs import ErrorLogService
    from account_pool.gateway_service import GatewayService, create_gateway_router
    from account_pool.quota_reset_api import create_quota_reset_router
    from account_pool.quota_reset_models import ResetVerification, ResetVerificationRecord
    from fastapi import FastAPI
    from test_gateway_service import MemoryKeys, MemoryLeases, MemoryLogs, MemoryPolicies, gateway

    from litellm.proxy.management_endpoints.account_pool_gateway import AccountPoolGatewayMiddleware
    from litellm.proxy.management_endpoints.account_pool_gateway_client import ManagerControl

    url, repo, store = database
    _, record, _, upstream = setup_service()
    await repo.save(record)
    await store.save_verification(record.id, ResetVerificationRecord(model="model-a", result=ResetVerification(passed=True, restored=True, reason="restored")))
    keys, logs = CardKeyService(MemoryKeys()), MemoryLogs()
    issued = await keys.issue(record.id)
    manager = FastAPI()
    manager.include_router(create_gateway_router(GatewayService(keys, repo, MemoryPolicies(), MemoryLeases(),
        ErrorLogService(logs), gateway, on_api_completed=store.confirm_api_call), lambda: None))
    manager.include_router(create_quota_reset_router(QuotaResetService(store, repo, upstream, CredentialOwnership(url)), lambda: None))
    body = ({"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]}
            if endpoint.endswith("responses") else {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})
    wire = ("data: " + json.dumps({"type": "response.completed", "response": body}) + "\n\n"
            if endpoint.endswith("responses") else 'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
    def respond(request):
        assert record.id.hex in request.url.host
        return httpx.Response(200, content=wire, headers={"content-type": "text/event-stream"}) if stream else httpx.Response(200, json=body)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(manager), base_url="http://manager") as manager_http:
        app = FastAPI()
        app.add_middleware(lambda app, **kwargs: AccountPoolGatewayMiddleware(app, **kwargs).dispatch_card,
            control_factory=lambda _: ManagerControl(manager_http, "http://manager", "fixture-manager"),
            client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://proxy") as client:
            response = await client.post(endpoint, headers={"Authorization": "Bearer " + issued.value.key}, json={
                "model": "model-a", "stream": stream,
                **({"input": "hello"} if endpoint.endswith("responses") else {"messages": [{"role": "user", "content": "hello"}]}),
            })
        assert response.status_code == 200 and "ok" in response.text
        status = await manager_http.get(f"/api/environments/{record.id}/quota-reset")
    saved = await PostgresResetStore(url).latest_api_call(record.id)
    assert saved is not None and saved.endpoint == endpoint
    assert status.json()["last_api_call"]["request_id"] == str(saved.request_id)
    assert saved.request_id == logs.events[-1].request_id
    assert logs.events[-1].final_status == "succeeded"
    assert upstream.calls == [] and upstream.probes == []
