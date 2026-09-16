"""本文件验证凭证归属唯一性、轮换身份、并发领取与操作锁释放。"""

from __future__ import annotations

import asyncio
import base64
import json
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
