"""本模块实现号池插件清单、安装状态和启停生命周期，插件始终在进程外运行。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Final, Literal, Protocol

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from account_pool.repository import database_connection


class PluginManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    plugin_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    display_name: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=64)
    runtime: Literal["sidecar"] = "sidecar"
    entrypoint: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    capabilities: tuple[str, ...] = Field(default=(), max_length=100)
    provider_families: tuple[str, ...] = Field(default=(), max_length=100)

    @field_validator("capabilities", "provider_families")
    @classmethod
    def normalize_items(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


class PluginRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    manifest: PluginManifest
    state: Literal["installed", "enabled", "disabled", "incompatible", "error"]
    installed_at: datetime
    updated_at: datetime
    last_error: str | None = None


class PluginRepository(Protocol):
    async def initialize(self) -> None: ...

    async def list(self) -> tuple[PluginRecord, ...]: ...

    async def get(self, plugin_id: str) -> PluginRecord | None: ...

    async def save(self, record: PluginRecord) -> PluginRecord: ...

    async def delete(self, plugin_id: str) -> None: ...


class PluginService:
    def __init__(self, repository: PluginRepository, registry: Sequence[PluginManifest] = ()) -> None:
        self._repository: Final = repository
        self._registry: Final = tuple(registry)

    async def installed(self) -> tuple[PluginRecord, ...]:
        return await self._repository.list()

    def store(self) -> tuple[PluginManifest, ...]:
        return self._registry

    async def install(self, manifest: PluginManifest) -> PluginRecord:
        now: Final = datetime.now(timezone.utc)
        current: Final = await self._repository.get(manifest.plugin_id)
        installed_at: Final = current.installed_at if current is not None else now
        state: Final = "installed" if manifest.runtime == "sidecar" else "incompatible"
        record: Final = PluginRecord(
            manifest=manifest,
            state=state,
            installed_at=installed_at,
            updated_at=now,
            last_error=None if state == "installed" else "plugin runtime is incompatible",
        )
        return await self._repository.save(record)

    async def set_enabled(self, plugin_id: str, enabled: bool) -> PluginRecord | None:
        current: Final = await self._repository.get(plugin_id)
        if current is None or current.state == "incompatible":
            return None
        return await self._repository.save(
            current.model_copy(update={"state": "enabled" if enabled else "disabled", "updated_at": datetime.now(timezone.utc)})
        )

    async def uninstall(self, plugin_id: str) -> bool:
        current: Final = await self._repository.get(plugin_id)
        if current is None:
            return False
        await self._repository.delete(plugin_id)
        return True


def parse_plugin_registry(raw: str) -> tuple[PluginManifest, ...]:
    """解析管理员配置的签名清单，格式错误时拒绝启动而不是静默信任未知插件。"""
    if not raw.strip():
        return ()
    payload: Final = json.loads(raw)
    return tuple(PluginManifest.model_validate(item) for item in payload)


class PostgresPluginRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url: Final = database_url

    async def initialize(self) -> None:
        async with database_connection(self._database_url) as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS account_pool_plugins (
                    plugin_id text PRIMARY KEY,
                    payload jsonb NOT NULL,
                    updated_at timestamptz NOT NULL
                )
                """
            )

    async def list(self) -> tuple[PluginRecord, ...]:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute("SELECT payload FROM account_pool_plugins ORDER BY plugin_id")
            rows: Final = await cursor.fetchall()
        return tuple(PluginRecord.model_validate(row["payload"]) for row in rows)

    async def get(self, plugin_id: str) -> PluginRecord | None:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT payload FROM account_pool_plugins WHERE plugin_id = %s", (plugin_id,)
            )
            row: Final = await cursor.fetchone()
        return None if row is None else PluginRecord.model_validate(row["payload"])

    async def save(self, record: PluginRecord) -> PluginRecord:
        payload: Final = record.model_dump(mode="json")
        async with database_connection(self._database_url) as connection:
            await connection.execute(
                """
                INSERT INTO account_pool_plugins (plugin_id, payload, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (plugin_id) DO UPDATE SET payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at
                """,
                (record.manifest.plugin_id, Jsonb(payload), record.updated_at),
            )
        return record

    async def delete(self, plugin_id: str) -> None:
        async with database_connection(self._database_url) as connection:
            await connection.execute("DELETE FROM account_pool_plugins WHERE plugin_id = %s", (plugin_id,))


__all__ = ("PluginManifest", "PluginRecord", "PluginService", "PostgresPluginRepository", "parse_plugin_registry")
