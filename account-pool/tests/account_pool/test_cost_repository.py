"""真实 PostgreSQL 验证账号存在性、并发幂等、作废和删除后的历史保留。"""

import asyncio
from datetime import date
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
    await store.renew_due(date(2026, 3, 1))
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


@pytest.mark.asyncio
async def test_automatic_renewal_catchup_concurrency_stop_void_and_legacy_json(database):
    from account_pool.cost_models import PoolCostRecord

    url, environments, _ = database
    store = PostgresCostRepository(url)
    await store.initialize()
    _, account, _, _ = setup_service()
    await environments.save(account)
    request = PoolCostCreate(
        id=uuid4(),
        category="subscription",
        account_id=account.id,
        name="月订阅",
        amount_cents=19900,
        paid_on="2026-01-01",
        starts_on="2026-01-01",
        ends_on="2026-01-31",
        auto_renew=True,
        cycle_months=1,
    )
    await store.create(request, "admin")
    await store.renew_due(date(2026, 1, 31))
    assert len(await store.list()) == 1
    await asyncio.gather(store.renew_due(date(2026, 3, 1)), PostgresCostRepository(url).renew_due(date(2026, 3, 1)))
    records = await store.list()
    assert len(records) == 3
    february = next(r for r in records if r.starts_on == date(2026, 2, 1))
    assert february.ends_on == date(2026, 2, 28) and february.paid_on == date(2026, 2, 1)
    assert february.created_by == "system:auto-renewal" and february.renewal_of == request.id
    assert all(r.amount_cents == 19900 for r in records)
    async with database_connection(url) as connection:
        cursor = await connection.execute("SELECT payload FROM account_pool_cost_records")
        for row in await cursor.fetchall():
            assert not {"auto_renew", "cycle_months", "renewal_of"}.intersection(row["payload"])
            PoolCostRecord.model_validate(row["payload"])
    await store.stop_renewal(request.id)
    await store.renew_due(date(2026, 7, 1))
    assert len(await store.list()) == 3
    assert not any(r.auto_renew for r in await store.list())
    again = request.model_copy(update={"id": uuid4(), "starts_on": date(2026, 4, 1), "ends_on": date(2026, 4, 30)})
    await store.create(again, "admin")
    await store.void(again.id, "取消", "admin")
    await store.renew_due(date(2026, 7, 1))
    assert len(await store.list()) == 4
    await store.create(request.model_copy(update={"id": uuid4()}), "admin")
    async with database_connection(url) as connection:
        await connection.execute("DELETE FROM account_pool_environments WHERE id = %s", (account.id,))
    await store.renew_due(date(2026, 7, 1))
    assert len(await store.list()) == 5


@pytest.mark.asyncio
async def test_replacement_is_atomic_audited_idempotent_and_used_by_reports_and_renewal(database):
    from account_pool.cost_models import PoolCostQuery
    from account_pool.costs import cost_report

    url, environments, _ = database
    store = PostgresCostRepository(url)
    await store.initialize()
    _, account, _, _ = setup_service()
    await environments.save(account)
    original = PoolCostCreate(id=uuid4(), category="subscription", account_id=account.id, name="月费",
        amount_cents=19900, paid_on="2026-01-01", starts_on="2026-01-01", ends_on="2026-01-31",
        cycle_months=1, auto_renew=True)
    await store.create(original, "admin")
    correction = original.model_copy(update={"id": uuid4(), "amount_cents": 9900, "note": "更正实付金额"})
    changed_scope = await store.replace(original.id, correction.model_copy(update={"account_id": uuid4()}), "editor")
    assert isinstance(changed_scope, CostFailure) and changed_scope.status == 422
    assert len(await store.list()) == 1 and (await store.list())[0].auto_renew
    first, retry = await asyncio.gather(store.replace(original.id, correction, "editor"),
        PostgresCostRepository(url).replace(original.id, correction, "editor"))
    assert first == retry and not isinstance(first, CostFailure)
    assert first.created_by == "editor" and first.auto_renew and first.note == correction.note
    records = await store.list()
    assert len(records) == 2
    old = next(r for r in records if r.id == original.id)
    assert old.voided_by == "editor" and old.voided_at and not old.auto_renew
    assert str(correction.id) in old.void_reason
    report = cost_report(records, {account.id: account.name},
        PoolCostQuery(starts_on="2026-01-01", ends_on="2026-01-31"), date(2026, 1, 10))
    assert report.paid.total_cents == report.period.total_cents == 9900
    assert report.accounts[0].latest.id == correction.id
    conflict = await store.replace(original.id, correction.model_copy(update={"id": uuid4()}), "editor")
    assert isinstance(conflict, CostFailure) and conflict.status == 409
    await store.renew_due(date(2026, 2, 1))
    active = [r for r in await store.list() if not r.voided_at]
    assert len(active) == 2 and all(r.amount_cents == 9900 for r in active)
    stale = await store.replace(correction.id, correction.model_copy(update={"id": uuid4()}), "editor")
    assert isinstance(stale, CostFailure) and stale.status == 409
    assert len(await store.list()) == 3


@pytest.mark.asyncio
async def test_replacement_failure_preserves_original_and_existing_unrelated_record(database):
    url, environments, _ = database
    store = PostgresCostRepository(url)
    await store.initialize()
    _, account, _, _ = setup_service()
    await environments.save(account)
    original = PoolCostCreate(id=uuid4(), category="subscription", account_id=account.id, name="月费",
        amount_cents=19900, paid_on="2026-01-01", starts_on="2026-01-01", ends_on="2026-01-31")
    saved = await store.create(original, "admin")
    other = original.model_copy(update={"id": uuid4(), "category": "server", "account_id": None})
    await store.create(other, "admin")
    collision = await store.replace(original.id, original.model_copy(update={"id": other.id}), "editor")
    assert isinstance(collision, CostFailure) and collision.status == 409
    async with database_connection(url) as connection:
        await connection.execute("DELETE FROM account_pool_environments WHERE id = %s", (account.id,))
    missing = await store.replace(original.id, original.model_copy(update={"id": uuid4()}), "editor")
    assert isinstance(missing, CostFailure) and missing.status == 404
    assert next(r for r in await store.list() if r.id == original.id) == saved
    assert len(await store.list()) == 2

    await environments.save(account)
    async with database_connection(url) as connection:
        await connection.execute("""CREATE FUNCTION reject_cost_update() RETURNS trigger LANGUAGE plpgsql AS
            $$ BEGIN RAISE EXCEPTION 'simulated update failure'; END $$""")
        await connection.execute("CREATE TRIGGER reject_cost_update BEFORE UPDATE ON account_pool_cost_records "
                                 "FOR EACH ROW EXECUTE FUNCTION reject_cost_update()")
    import psycopg

    with pytest.raises(psycopg.errors.RaiseException, match="simulated update failure"):
        await store.replace(original.id, original.model_copy(update={"id": uuid4(), "auto_renew": True, "cycle_months": 1}), "editor")
    assert len(await store.list()) == 2
    assert next(r for r in await store.list() if r.id == original.id) == saved
