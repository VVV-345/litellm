"""在隔离 PostgreSQL schema 验证上号任务指纹写入、并发去重和失效释放。"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Final
from uuid import UUID, uuid4

import psycopg
import pytest
from account_pool.onboarding_models import OnboardingItem
from account_pool.onboarding_repository import PostgresOnboardingRepository, StoredOnboarding
from psycopg import sql
from psycopg.conninfo import make_conninfo

TEST_DATABASE_URL: Final = "ONBOARDING_TEST_DATABASE_URL"


def stored(fingerprints: tuple[str, ...], card_id: UUID | None = None) -> StoredOnboarding:
    now: Final = datetime.now(timezone.utc)
    item_id: Final = uuid4()
    return StoredOnboarding(
        item=OnboardingItem(
            id=item_id,
            job_id=uuid4(),
            source="auth_file",
            supplier="openai_codex",
            label="account.json",
            state="ready",
            message="ready",
            card_id=card_id or uuid4(),
            created_at=now,
            updated_at=now,
        ),
        ciphertext="sealed",
        fingerprints=fingerprints,
    )


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get(TEST_DATABASE_URL), reason="isolated PostgreSQL URL required")
async def test_insert_releases_stale_fingerprints_in_the_same_transaction() -> None:
    base: Final = os.environ[TEST_DATABASE_URL]
    schema: Final = "onboarding_test_" + uuid4().hex
    async with await psycopg.AsyncConnection.connect(base, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            dsn: Final = make_conninfo(base, options=f"-c search_path={schema}")
            repository: Final = PostgresOnboardingRepository(dsn)
            await repository.initialize()
            first: Final = stored(("email", "old-token"))
            second: Final = stored(("other-email", "second-token"))
            assert await repository.insert(first)
            assert await repository.insert(second)

            replacement: Final = stored(("email", "new-token"))
            assert await repository.insert(replacement, release_ids=(first.item.id, second.item.id))

            rows: Final = {entry.item.id: entry for entry in await repository.list()}
            assert rows[first.item.id].fingerprints == ()
            assert rows[second.item.id].fingerprints == ()
            assert rows[replacement.item.id].fingerprints == ("email", "new-token")

            live: Final = stored(("live-email", "live-token"))
            blocked: Final = stored(("live-email", "other-token"))
            assert await repository.insert(live)
            assert not await repository.insert(blocked)
        finally:
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
