"""本文件验证凭证归属唯一性、轮换身份、并发领取与操作锁释放。"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from typing import Final
from uuid import uuid4

import pytest
from account_pool.credential_ownership import CredentialConflict, CredentialOwnership, credential_identity
from account_pool.secrets import EnvironmentSecretDeriver


@pytest.mark.asyncio
async def test_concurrent_claim_allows_only_one_owner_and_release_allows_transfer() -> None:
    registry: Final = CredentialOwnership()
    first: Final = uuid4()
    second: Final = uuid4()
    results: Final = await asyncio.gather(
        registry.claim(first, ("token", "email")), registry.claim(second, ("token", "other")), return_exceptions=True
    )
    assert sum(isinstance(result, CredentialConflict) for result in results) == 1
    assert await registry.owns(first, ("token", "email"))
    assert not await registry.owns(second, ("other",))
    await registry.retain(first)
    await registry.claim(second, ("token", "other"))
    assert await registry.owns(second, ("token", "other"))


@pytest.mark.asyncio
async def test_rotated_token_and_renamed_file_still_belong_to_the_same_account() -> None:
    registry: Final = CredentialOwnership()
    secrets: Final = EnvironmentSecretDeriver("s" * 32)
    before: Final = credential_identity(b'{"refresh_token":"old","email":"User@example.test"}', "codex", secrets)
    after: Final = credential_identity(b'{"refresh_token":"new","email":"user@example.test"}', "codex", secrets)
    owner: Final = uuid4()
    await registry.claim(owner, before.fingerprints)
    with pytest.raises(CredentialConflict, match="已绑定其他卡片"):
        await registry.claim(uuid4(), after.fingerprints)
    await registry.claim(owner, after.fingerprints)
    assert await registry.owns(owner, after.fingerprints)
    assert all("old" not in fingerprint and "example" not in fingerprint for fingerprint in before.fingerprints)


def test_identity_decodes_account_without_returning_tokens() -> None:
    claims: Final = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"email": "identity@example.test", "https://api.openai.com/auth": {"chatgpt_account_id": "account-123"}}
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    identity: Final = credential_identity(
        json.dumps({"id_token": f"header.{claims}.signature", "refresh_token": "secret-refresh"}).encode(),
        "codex",
        EnvironmentSecretDeriver("s" * 32),
    )
    assert identity.email == "identity@example.test"
    assert identity.account_id == "account-123"
    assert "secret-refresh" not in repr(identity)


@pytest.mark.asyncio
async def test_same_key_cannot_bypass_ownership_by_changing_provider() -> None:
    secrets: Final = EnvironmentSecretDeriver("s" * 32)
    registry: Final = CredentialOwnership()
    first: Final = credential_identity(b'{"api_key":"secret"}', "gemini", secrets)
    second: Final = credential_identity(b'{"api_key":"secret"}', "openai_compatible", secrets)
    await registry.claim(uuid4(), first.fingerprints)
    with pytest.raises(CredentialConflict, match="已绑定其他卡片"):
        await registry.claim(uuid4(), second.fingerprints)


@pytest.mark.asyncio
async def test_operation_lock_waits_and_releases_after_cancellation() -> None:
    registry: Final = CredentialOwnership()
    owner: Final = uuid4()
    entered: Final = asyncio.Event()
    waiting: Final = asyncio.Event()

    async def first() -> None:
        async with registry.operation(owner):
            entered.set()
            await asyncio.Event().wait()

    async def second() -> None:
        async with registry.operation(owner):
            waiting.set()

    first_task: Final = asyncio.create_task(first())
    await asyncio.wait_for(entered.wait(), 1)
    second_task: Final = asyncio.create_task(second())
    await asyncio.sleep(0)
    assert not waiting.is_set()
    first_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_task
    await asyncio.wait_for(second_task, 1)
    assert waiting.is_set()


@pytest.mark.asyncio
async def test_access_only_codex_import_retains_exclusive_token_and_account_ownership() -> None:
    registry: Final = CredentialOwnership()
    secrets: Final = EnvironmentSecretDeriver("s" * 32)
    access_only: Final = credential_identity(
        b'{"access_token":"access-a","account_id":"same-account"}', "openai_codex", secrets
    )
    rotated: Final = credential_identity(
        b'{"access_token":"access-b","account_id":"same-account"}', "openai_codex", secrets
    )
    owner: Final = uuid4()
    await registry.claim(owner, access_only.fingerprints)
    with pytest.raises(CredentialConflict, match="已绑定其他卡片"):
        await registry.claim(uuid4(), rotated.fingerprints)
    await registry.claim(owner, rotated.fingerprints)
    assert await registry.owns(owner, rotated.fingerprints)
    assert "access-a" not in repr(access_only)
    with pytest.raises(CredentialConflict):
        credential_identity(b'{"account_id":"same-account"}', "openai_codex", secrets)


@pytest.mark.asyncio
@pytest.mark.parametrize("full_first", (False, True))
async def test_access_only_and_full_codex_credentials_cannot_claim_the_same_access(full_first: bool) -> None:
    secrets: Final = EnvironmentSecretDeriver("s" * 32)
    registry: Final = CredentialOwnership()
    access: Final = credential_identity(b'{"access_token":"fixture-shared-access"}', "openai_codex", secrets)
    full: Final = credential_identity(
        b'{"access_token":"fixture-shared-access","refresh_token":"fixture-refresh"}', "openai_codex", secrets
    )
    first, second = (full, access) if full_first else (access, full)
    owner: Final = uuid4()
    await registry.claim(owner, first.fingerprints)
    with pytest.raises(CredentialConflict, match="已绑定其他卡片"):
        await registry.claim(uuid4(), second.fingerprints)
    await registry.claim(owner, second.fingerprints)
    assert await registry.owns(owner, (*first.fingerprints, *second.fingerprints))


@pytest.mark.asyncio
@pytest.mark.parametrize("postgres", (False, True))
async def test_batch_ownership_requires_all_fingerprints_on_the_exact_owner(postgres: bool) -> None:
    url: Final = os.environ.get("ACCOUNT_POOL_TEST_DATABASE_URL") if postgres else None
    if postgres and not url:
        pytest.skip("requires an isolated ACCOUNT_POOL_TEST_DATABASE_URL")
    registry: Final = CredentialOwnership(url)
    await registry.initialize()
    first, second, partial, stolen, empty = (uuid4() for _ in range(5))
    prefix: Final = uuid4().hex
    primary, secondary, missing, partial_key = (prefix + suffix for suffix in ("-a", "-b", "-missing", "-partial"))
    await registry.claim(first, (primary, secondary))
    await registry.claim(second, (prefix + "-second",))
    await registry.claim(partial, (partial_key,))
    assert await registry.owns_many({
        first: (primary, primary, secondary), second: (prefix + "-second",),
        partial: (partial_key, missing), stolen: (primary,), empty: (),
    }) == frozenset((first, second))
    assert await registry.owns_many({}) == frozenset()


@pytest.mark.asyncio
async def test_operation_budget_rejects_seventh_without_blocking_model_queries() -> None:
    from contextlib import AsyncExitStack

    from account_pool.repository import PostgresEnvironmentRepository, database_pool
    from psycopg_pool import PoolTimeout

    url: Final = os.environ.get("ACCOUNT_POOL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires an isolated ACCOUNT_POOL_TEST_DATABASE_URL")
    registry: Final = CredentialOwnership(url)
    local_profile: Final = asyncio.Lock()
    async with database_pool(url, max_size=1) as main_pool:
        environments: Final = PostgresEnvironmentRepository(url)
        await environments.initialize()
        async with local_profile, AsyncExitStack() as locks:
            for _ in range(6):
                await locks.enter_async_context(registry.operation(uuid4()))
            async with asyncio.timeout(0.1):
                with pytest.raises(PoolTimeout):
                    async with registry.operation(uuid4()):
                        pytest.fail("seventh long operation exceeded its independent budget")
            async with asyncio.timeout(0.5):
                assert await environments.get(uuid4()) is None
            assert main_pool.get_stats()["pool_size"] == 1
        async with registry.operation(uuid4()), registry.operation(uuid4()), registry.operation(uuid4()):
            assert await environments.get(uuid4()) is None


@pytest.mark.asyncio
async def test_nested_operation_exhaustion_releases_existing_advisory_lock() -> None:
    from contextlib import AsyncExitStack

    from account_pool.repository import database_connection, database_pool
    from psycopg_pool import PoolTimeout

    url: Final = os.environ.get("ACCOUNT_POOL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires an isolated ACCOUNT_POOL_TEST_DATABASE_URL")
    registry: Final = CredentialOwnership(url)
    outer: Final = uuid4()
    async with database_pool(url), AsyncExitStack() as occupied:
        for _ in range(5):
            await occupied.enter_async_context(registry.operation(uuid4()))
        with pytest.raises(PoolTimeout):
            async with registry.operation(outer), registry.operation(uuid4()):
                pytest.fail("nested operation unexpectedly exceeded the budget")
        async with database_connection(url) as connection:
            cursor: Final = await connection.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0)) AS released", (str(outer),)
            )
            row: Final = await cursor.fetchone()
            assert row is not None and row["released"] is True
        async with registry.operation(outer):
            pass


@pytest.mark.asyncio
async def test_child_task_cannot_inherit_lock_budget_and_cancelled_waiter_returns_slot() -> None:
    from contextlib import AsyncExitStack

    from account_pool.repository import database_connection, database_pool
    from psycopg_pool import PoolTimeout

    url: Final = os.environ.get("ACCOUNT_POOL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires an isolated ACCOUNT_POOL_TEST_DATABASE_URL")
    registry: Final = CredentialOwnership(url)

    async def child(owner) -> None:
        async with registry.operation(owner):
            pass

    async with database_pool(url):
        async with AsyncExitStack() as occupied:
            owners: Final = tuple(uuid4() for _ in range(6))
            for owner in owners:
                await occupied.enter_async_context(registry.operation(owner))
            with pytest.raises(PoolTimeout):
                await asyncio.create_task(child(uuid4()))
        waiting_owner: Final = uuid4()
        async with registry.operation(waiting_owner):
            waiter: Final = asyncio.create_task(child(waiting_owner))
            await asyncio.sleep(0.05)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        async with AsyncExitStack() as occupied_again:
            for _ in range(6):
                await occupied_again.enter_async_context(registry.operation(uuid4()))
            async with database_connection(url) as connection:
                await connection.execute("SELECT 1")
