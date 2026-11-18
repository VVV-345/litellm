"""真实 PostgreSQL 验证账号存在性、并发幂等、作废和删除后的历史保留。"""

import asyncio
from uuid import uuid4

import pytest
from account_pool.cost_models import PoolCostCreate
from account_pool.cost_repository import CostFailure, PostgresCostRepository
from account_pool.repository import database_connection
from test_quota_reset import setup_service

pytest_plugins = ("test_quota_reset_postgres",)


@pytest.mark.asyncio
async def test_account_validation_concurrent_retry_void_and_history(database):
    url, environments, _ = database
    store = PostgresCostRepository(url)
    await store.initialize()
    _, account, _, _ = setup_service()
    request = PoolCostCreate(
        id=uuid4(),
        category="subscription",
        account_id=account.id,
        name="月订阅",
        amount_cents=19900,
        paid_on="2026-01-01",
        starts_on="2026-01-01",
        ends_on="2026-01-31",
    )
    assert isinstance(await store.create(request, "admin"), CostFailure)
    await environments.save(account)
    first, second = await asyncio.gather(
        store.create(request, "admin"), PostgresCostRepository(url).create(request, "admin")
    )
    assert first == second
    assert len(await store.list()) == 1
    conflict = await store.create(request.model_copy(update={"amount_cents": 999}), "admin")
    assert isinstance(conflict, CostFailure) and conflict.status == 409
    async with database_connection(url) as connection:
        await connection.execute("DELETE FROM account_pool_environments WHERE id = %s", (account.id,))
    assert (await store.list())[0].account_name == account.name
    assert await store.create(request, "admin") == first
    assert isinstance(await store.create(request.model_copy(update={"id": uuid4()}), "admin"), CostFailure)
    voided = await store.void(request.id, "金额录错", "admin:other")
    assert not isinstance(voided, CostFailure)
    assert voided.voided_by == "admin:other"
    assert await store.void(request.id, "重复点击", "admin") == voided
    assert (await store.list())[0].void_reason == "金额录错"
    proxy = request.model_copy(update={"id": uuid4(), "category": "proxy", "account_id": None})
    assert not isinstance(await store.create(proxy, "admin"), CostFailure)
