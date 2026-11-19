"""在隔离 PostgreSQL 验证跨实例分配、重启持久化及运行锁不留下长事务。"""

import asyncio
import os
from uuid import uuid4

import pytest
from account_pool.email_rebinding.models import PoolUpload, ProposeBinding
from account_pool.email_rebinding.repository import PostgresBindingRepository
from account_pool.email_rebinding.service import BindingService
from account_pool.repository import database_connection, database_pool
from account_pool.shared.result import Failure, Success
from test_email_rebinding import bindings as bindings

pytestmark = pytest.mark.skipif(not os.getenv("ACCOUNT_POOL_TEST_DATABASE_URL"), reason="requires isolated PostgreSQL")


@pytest.mark.asyncio
async def test_two_manager_instances_cannot_reserve_same_mailbox_and_restart_preserves_marker(bindings):
    memory, card, profiles = bindings
    url = os.environ["ACCOUNT_POOL_TEST_DATABASE_URL"]
    assert "/account_pool_test" in url
    first, second = PostgresBindingRepository(url), PostgresBindingRepository(url)
    await first.initialize()
    async with database_connection(url) as connection:
        await connection.execute("DELETE FROM account_pool_email_rebinding")
    alternate = card.model_copy(update={"id": uuid4(), "credential_email": "second@example.test"})
    memory.automation.environments.records[alternate.id] = alternate
    profile = profiles.state.profiles[0]
    profiles.state = profiles.state.model_copy(
        update={
            "profiles": (
                *profiles.state.profiles,
                profile.model_copy(
                    update={
                        "item": profile.item.model_copy(update={"id": uuid4(), "bound_environment_id": alternate.id}),
                        "account_digest": memory.automation.identity(alternate.credential_email),
                        "login_ciphertext": memory.cipher.seal(
                            alternate.id, "second@example.test----pwd----JBSWY3DPEHPK3PXP"
                        ),
                    }
                ),
            )
        }
    )
    a, b = BindingService(first, memory.automation), BindingService(second, memory.automation)
    await a.upload(PoolUpload(content="new@outlook.com----private-password----client----private-rt"))
    results = await asyncio.gather(
        a.propose(ProposeBinding(id=uuid4(), environment_id=card.id)),
        b.propose(ProposeBinding(id=uuid4(), environment_id=alternate.id)),
    )
    assert sum(isinstance(r, Success) for r in results) == 1
    assert sum(isinstance(r, Failure) for r in results) == 1
    restarted = BindingService(PostgresBindingRepository(url), memory.automation)
    snapshot = await restarted.repository.read()
    assert len(snapshot.jobs) == 1 and snapshot.mailboxes[0].item.status == "reserved"
    assert "private-password" not in snapshot.model_dump_json() and "private-rt" not in snapshot.model_dump_json()
    await restarted.upload(PoolUpload(content="new@outlook.com----replacement----client----replacement-rt"))
    secret = await restarted.mail_secret(snapshot.mailboxes[0].item.id)
    assert secret.refresh_token == "private-rt" and (await restarted.view()).mailboxes[0].status == "reserved"


@pytest.mark.asyncio
async def test_worker_lock_is_session_scoped_without_idle_transaction_and_released_on_cancel():
    url = os.environ["ACCOUNT_POOL_TEST_DATABASE_URL"]
    first, second = PostgresBindingRepository(url), PostgresBindingRepository(url)
    entered, hold = asyncio.Event(), asyncio.Event()

    async def worker():
        async with first.worker_lock() as acquired:
            assert acquired
            entered.set()
            await hold.wait()

    async with database_pool(url, max_size=2):
        task = asyncio.create_task(worker())
        await asyncio.wait_for(entered.wait(), 5)
        try:
            async with second.worker_lock() as acquired:
                assert acquired is False
            async with database_connection(url) as connection:
                cursor = await connection.execute(
                    "SELECT a.state,a.xact_start FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid "
                    "WHERE l.locktype='advisory' AND l.classid=714205998 AND l.objid=2 AND l.granted"
                )
                row = await cursor.fetchone()
                assert row and row["state"] == "idle" and row["xact_start"] is None
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        async with second.worker_lock() as acquired:
            assert acquired
