"""仅在显式测试数据库中验证跨实例兑换锁及恢复事务，不连接生产服务。"""

import asyncio
import os
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from account_pool.credential_ownership import CredentialOwnership
from account_pool.domain import EnvironmentStatus, ModelCooldown, QuotaSnapshot, QuotaWindow, utc_now
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
    assert len(upstream.calls) == 2  # One uncertain dispatch, then one same-identity reconciliation.
    assert len({item[1] for item in upstream.calls}) == 1
    assert (await store.get(record.id, left.request_id)).status == "already_redeemed"
    assert await store.get(uuid4(), left.request_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["none", "changed", "lease", "runtime"])
async def test_postgres_verified_restore_preserves_concurrent_or_independent_blocks(database, blocker):
    url, repo, _ = database
    _, record, _, _ = setup_service()
    other = ModelCooldown(model="other", reason="rate_limit", retry_at=utc_now() + timedelta(hours=1))
    selected = ModelCooldown(model="model-a", reason="rate_limit", retry_at=utc_now() + timedelta(hours=1))
    before = record.model_copy(
        update={
            "status": EnvironmentStatus.COOLING_DOWN,
            "automatic_cooldown": True,
            "last_error": "上游认证失效，请重新认证",
            "model_cooldowns": (selected, other),
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
    restored = await repo.restore_verified(before, "model-a", fresh)
    after = await repo.get(record.id)
    assert restored == (blocker == "none")
    if restored:
        assert after.status is EnvironmentStatus.READY
        assert after.model_cooldowns == (other,)
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
