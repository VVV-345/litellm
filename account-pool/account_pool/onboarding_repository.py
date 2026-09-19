"""持久化上号任务及加密载荷，用数据库锁防止重启和多进程重复执行。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Final, Protocol
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from account_pool.onboarding_models import OnboardingItem, OnboardingTarget
from account_pool.repository import database_connection


class StoredOnboarding(BaseModel):
    model_config = ConfigDict(frozen=True)
    item: OnboardingItem
    ciphertext: str = Field(repr=False)
    fingerprints: tuple[str, ...] = Field(repr=False)


class OnboardingRepository(Protocol):
    def worker_lock(self) -> AbstractAsyncContextManager[bool]: ...
    async def list(self) -> tuple[StoredOnboarding, ...]: ...
    async def get(self, item_id: UUID) -> StoredOnboarding | None: ...
    async def insert(self, row: StoredOnboarding) -> bool: ...
    async def save(self, row: StoredOnboarding) -> None: ...
    async def targets(self) -> tuple[OnboardingTarget, ...]: ...
    async def save_target(self, target: OnboardingTarget) -> None: ...


class PostgresOnboardingRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url: Final = database_url

    async def initialize(self) -> None:
        async with database_connection(self._database_url) as connection:
            await connection.execute(
                "CREATE TABLE IF NOT EXISTS account_pool_onboarding ("
                "id uuid PRIMARY KEY, payload jsonb NOT NULL, ciphertext text NOT NULL, "
                "fingerprints text[] NOT NULL, created_at timestamptz NOT NULL)"
            )
            await connection.execute(
                "CREATE TABLE IF NOT EXISTS account_pool_onboarding_targets (supplier text PRIMARY KEY, payload jsonb NOT NULL)"
            )

    @asynccontextmanager
    async def worker_lock(self) -> AsyncIterator[bool]:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute("SELECT pg_try_advisory_xact_lock(714205992) AS acquired")
            row: Final = await cursor.fetchone()
            yield row is not None and row["acquired"] is True

    async def list(self) -> tuple[StoredOnboarding, ...]:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute("SELECT * FROM account_pool_onboarding ORDER BY created_at, id")
            return tuple(self._decode(row) for row in await cursor.fetchall())

    async def get(self, item_id: UUID) -> StoredOnboarding | None:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute("SELECT * FROM account_pool_onboarding WHERE id = %s", (item_id,))
            row: Final = await cursor.fetchone()
            return None if row is None else self._decode(row)

    @staticmethod
    def _decode(values: Mapping[str, object]) -> StoredOnboarding:
        return StoredOnboarding(
            item=OnboardingItem.model_validate(values["payload"]),
            ciphertext=TypeAdapter(str).validate_python(values["ciphertext"]),
            fingerprints=TypeAdapter(tuple[str, ...]).validate_python(values["fingerprints"]),
        )

    async def insert(self, row: StoredOnboarding) -> bool:
        async with database_connection(self._database_url) as connection:
            # 同批、跨批和并发导入共用指纹锁，不能先查后写留下竞争窗口。
            await connection.execute("SELECT pg_advisory_xact_lock(714205993)")
            cursor: Final = await connection.execute(
                "INSERT INTO account_pool_onboarding (id, payload, ciphertext, fingerprints, created_at) "
                "SELECT %s, %s, %s, %s, %s WHERE NOT EXISTS "
                "(SELECT 1 FROM account_pool_onboarding WHERE fingerprints && %s) "
                "ON CONFLICT (id) DO NOTHING RETURNING id",
                (
                    row.item.id,
                    Jsonb(row.item.model_dump(mode="json")),
                    row.ciphertext,
                    list(row.fingerprints),
                    row.item.created_at,
                    list(row.fingerprints),
                ),
            )
            return await cursor.fetchone() is not None

    async def save(self, row: StoredOnboarding) -> None:
        async with database_connection(self._database_url) as connection:
            await connection.execute(
                "UPDATE account_pool_onboarding SET payload = %s, ciphertext = %s WHERE id = %s",
                (Jsonb(row.item.model_dump(mode="json")), row.ciphertext, row.item.id),
            )

    async def targets(self) -> tuple[OnboardingTarget, ...]:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT payload FROM account_pool_onboarding_targets ORDER BY supplier"
            )
            return tuple(OnboardingTarget.model_validate(row["payload"]) for row in await cursor.fetchall())

    async def save_target(self, target: OnboardingTarget) -> None:
        async with database_connection(self._database_url) as connection:
            await connection.execute(
                "INSERT INTO account_pool_onboarding_targets (supplier, payload) VALUES (%s, %s) "
                "ON CONFLICT (supplier) DO UPDATE SET payload = EXCLUDED.payload",
                (target.supplier, Jsonb(target.model_dump(mode="json"))),
            )
