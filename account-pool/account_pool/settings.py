"""本模块保存号池全局默认配置及其版本历史，禁止存储密钥和凭据内容。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Final, Literal, Protocol
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from account_pool.repository import database_connection


class StreamingRule(BaseModel):
    """流式传输规则，将一个规则绑定到若干号池卡片。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    mode: Literal["enabled", "disabled"] = "enabled"
    card_ids: tuple[UUID, ...] = Field(default=(), max_length=100)


class OAuthRequestScopedErrorRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    status: int = Field(default=0, ge=0, le=599)
    match: tuple[str, ...] = Field(default=(), max_length=100)
    match_regexr: tuple[str, ...] = Field(default=(), max_length=100, alias="match-regexr")
    action: Literal["stop", "stop-and-cooldown", "continue", "continue-and-cooldown"] = "continue"


class PayloadModelRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1, max_length=256)
    protocol: str = Field(default="", max_length=80)
    headers: Mapping[str, str] = Field(default_factory=dict)
    from_protocol: str = Field(default="", max_length=80, alias="from-protocol")
    match: tuple[Mapping[str, object], ...] = Field(default=())
    not_match: tuple[Mapping[str, object], ...] = Field(default=(), alias="not-match")
    exist: tuple[str, ...] = Field(default=())
    not_exist: tuple[str, ...] = Field(default=(), alias="not-exist")


class PayloadRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    models: tuple[PayloadModelRule, ...] = Field(default=(), max_length=100)
    params: Mapping[str, object] = Field(default_factory=dict)


class PayloadFilterRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    models: tuple[PayloadModelRule, ...] = Field(default=(), max_length=100)
    params: tuple[str, ...] = Field(default=(), max_length=500)


class PayloadSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    default: tuple[PayloadRule, ...] = Field(default=())
    default_raw: tuple[PayloadRule, ...] = Field(default=(), alias="default-raw")
    override: tuple[PayloadRule, ...] = Field(default=())
    override_raw: tuple[PayloadRule, ...] = Field(default=(), alias="override-raw")
    filter: tuple[PayloadFilterRule, ...] = Field(default=())


class AccountPoolSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    default_route: Literal["auto", "priority", "random", "quota"] = "auto"
    default_concurrency_limit: int = Field(default=1, ge=1, le=1000)
    default_model_discovery: bool = True
    default_proxy_profile_id: str | None = Field(default=None, max_length=120)
    max_attempts: int = Field(default=1, ge=1, le=5)
    request_timeout_seconds: int = Field(default=120, ge=1, le=3600)
    file_logging_enabled: bool = False
    debug_logging_enabled: bool = False
    websocket_enabled: bool = False
    request_log_enabled: bool = False
    websocket_auth_enabled: bool = False
    force_model_prefix: bool = False
    request_retry: int = Field(default=1, ge=0, le=20)
    max_retry_credentials: int = Field(default=1, ge=0, le=100)
    max_retry_interval: int = Field(default=0, ge=0, le=3600)
    usage_statistics_enabled: bool = False
    logs_max_total_size_mb: int = Field(default=0, ge=0, le=100000)
    error_logs_max_files: int = Field(default=10, ge=0, le=10000)
    quota_switch_project: bool = False
    quota_switch_preview_model: bool = False
    oauth_excluded_models: tuple[str, ...] = Field(default=(), max_length=500)
    oauth_model_aliases: Mapping[str, tuple[tuple[str, str], ...]] = Field(default_factory=dict)
    oauth_request_scoped_errors: Mapping[str, tuple[OAuthRequestScopedErrorRule, ...]] = Field(default_factory=dict)
    payload: PayloadSettings = Field(default_factory=PayloadSettings)
    plugins_enabled: bool = False
    streaming_rules: tuple[StreamingRule, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def unique_streaming_cards(self) -> AccountPoolSettings:
        cards: Final = tuple(card_id for rule in self.streaming_rules for card_id in rule.card_ids)
        if len(cards) != len(frozenset(cards)):
            raise ValueError("A card cannot be assigned to multiple streaming rules")
        return self


class AccountPoolSettingsView(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int = Field(ge=0)
    values: AccountPoolSettings
    updated_at: datetime | None = None
    requires_reload: bool = False


class AccountPoolSettingsUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=0)
    values: AccountPoolSettings


class AccountPoolSettingsHistoryEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int = Field(ge=0)
    values: AccountPoolSettings
    created_at: datetime
    source: Literal["initial", "update", "rollback"]


class AccountPoolSettingsChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    previous: str
    proposed: str


class AccountPoolSettingsPreview(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_version: int = Field(ge=0)
    affected_card_ids: tuple[UUID, ...]
    changes: tuple[AccountPoolSettingsChange, ...]
    requires_reload: bool


class AccountPoolSettingsRepository(Protocol):
    async def initialize(self) -> None: ...

    async def get(self) -> AccountPoolSettingsView: ...

    async def save(self, request: AccountPoolSettingsUpdate) -> AccountPoolSettingsView | None: ...

    async def history(self) -> tuple[AccountPoolSettingsHistoryEntry, ...]: ...

    async def rollback(self, expected_version: int, target_version: int) -> AccountPoolSettingsView | None: ...


_SCHEMA: Final = (
    """
    CREATE TABLE IF NOT EXISTS account_pool_settings_history (
        version integer PRIMARY KEY,
        payload jsonb NOT NULL,
        created_at timestamptz NOT NULL,
        source text NOT NULL CHECK (source IN ('initial', 'update', 'rollback'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS account_pool_settings (
        singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
        version integer NOT NULL,
        payload jsonb NOT NULL,
        updated_at timestamptz NOT NULL
    )
    """,
)


class PostgresAccountPoolSettingsRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url: Final = database_url

    async def initialize(self) -> None:
        async with database_connection(self._database_url) as connection:
            for statement in _SCHEMA:
                await connection.execute(statement)
            now: Final = datetime.now(timezone.utc)
            defaults: Final = AccountPoolSettings()
            await connection.execute(
                """
                INSERT INTO account_pool_settings (singleton, version, payload, updated_at)
                VALUES (true, 0, %s, %s)
                ON CONFLICT (singleton) DO NOTHING
                """,
                (Jsonb(defaults.model_dump(mode="json")), now),
            )
            await connection.execute(
                """
                INSERT INTO account_pool_settings_history (version, payload, created_at, source)
                VALUES (0, %s, %s, 'initial')
                ON CONFLICT (version) DO NOTHING
                """,
                (Jsonb(defaults.model_dump(mode="json")), now),
            )

    async def get(self) -> AccountPoolSettingsView:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute("SELECT version, payload, updated_at FROM account_pool_settings")
            row: Final = await cursor.fetchone()
        if row is None:
            return AccountPoolSettingsView(version=0, values=AccountPoolSettings())
        return AccountPoolSettingsView(
            version=int(row["version"]),
            values=AccountPoolSettings.model_validate(row["payload"]),
            updated_at=row["updated_at"],
        )

    async def save(self, request: AccountPoolSettingsUpdate) -> AccountPoolSettingsView | None:
        async with database_connection(self._database_url) as connection:
            current: Final = await connection.execute(
                "SELECT version, payload FROM account_pool_settings WHERE singleton FOR UPDATE"
            )
            row: Final = await current.fetchone()
            if row is None or int(row["version"]) != request.version:
                return None
            now: Final = datetime.now(timezone.utc)
            previous: Final = AccountPoolSettings.model_validate(row["payload"])
            next_version: Final = request.version + 1
            await connection.execute(
                "UPDATE account_pool_settings SET version = %s, payload = %s, updated_at = %s WHERE singleton",
                (next_version, Jsonb(request.values.model_dump(mode="json")), now),
            )
            await connection.execute(
                "INSERT INTO account_pool_settings_history (version, payload, created_at, source) VALUES (%s, %s, %s, 'update')",
                (next_version, Jsonb(request.values.model_dump(mode="json")), now),
            )
        return AccountPoolSettingsView(
            version=next_version,
            values=request.values,
            updated_at=now,
            requires_reload=_requires_reload_transition(previous, request.values),
        )

    async def history(self) -> tuple[AccountPoolSettingsHistoryEntry, ...]:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT version, payload, created_at, source FROM account_pool_settings_history ORDER BY version DESC LIMIT 100"
            )
            rows: Final = await cursor.fetchall()
        return tuple(
            AccountPoolSettingsHistoryEntry(
                version=int(row["version"]),
                values=AccountPoolSettings.model_validate(row["payload"]),
                created_at=row["created_at"],
                source=row["source"],
            )
            for row in rows
        )

    async def rollback(self, expected_version: int, target_version: int) -> AccountPoolSettingsView | None:
        async with database_connection(self._database_url) as connection:
            current: Final = await connection.execute(
                "SELECT version, payload FROM account_pool_settings WHERE singleton FOR UPDATE"
            )
            current_row: Final = await current.fetchone()
            if current_row is None or int(current_row["version"]) != expected_version:
                return None
            current_values: Final = AccountPoolSettings.model_validate(current_row["payload"])
            target: Final = await connection.execute(
                "SELECT payload FROM account_pool_settings_history WHERE version = %s", (target_version,)
            )
            target_row: Final = await target.fetchone()
            if target_row is None:
                return None
            values: Final = AccountPoolSettings.model_validate(target_row["payload"])
            now: Final = datetime.now(timezone.utc)
            next_version: Final = expected_version + 1
            await connection.execute(
                "UPDATE account_pool_settings SET version = %s, payload = %s, updated_at = %s WHERE singleton",
                (next_version, Jsonb(values.model_dump(mode="json")), now),
            )
            await connection.execute(
                "INSERT INTO account_pool_settings_history (version, payload, created_at, source) VALUES (%s, %s, %s, 'rollback')",
                (next_version, Jsonb(values.model_dump(mode="json")), now),
            )
        return AccountPoolSettingsView(
            version=next_version,
            values=values,
            updated_at=now,
            requires_reload=_requires_reload_transition(current_values, values),
        )


def settings_preview(
    current: AccountPoolSettingsView,
    proposed: AccountPoolSettings,
    card_ids: Sequence[UUID],
) -> AccountPoolSettingsPreview:
    old_values: Final = current.values.model_dump(mode="json")
    new_values: Final = proposed.model_dump(mode="json")
    changes: Final = tuple(
        AccountPoolSettingsChange(key=key, previous=str(old_values[key]), proposed=str(new_values[key]))
        for key in old_values
        if old_values[key] != new_values[key]
    )
    return AccountPoolSettingsPreview(
        base_version=current.version,
        affected_card_ids=tuple(card_ids),
        changes=changes,
        requires_reload=_requires_reload_transition(current.values, proposed),
    )


def _requires_reload(values: AccountPoolSettings) -> bool:
    return values.file_logging_enabled or values.websocket_enabled or values.plugins_enabled


def _requires_reload_transition(previous: AccountPoolSettings, proposed: AccountPoolSettings) -> bool:
    reload_keys: Final = frozenset(("file_logging_enabled", "websocket_enabled", "plugins_enabled"))
    previous_values: Final = previous.model_dump(mode="json")
    proposed_values: Final = proposed.model_dump(mode="json")
    return any(
        previous_values[key] != proposed_values[key] for key in reload_keys
    ) or _requires_reload(proposed)


__all__ = (
    "AccountPoolSettings",
    "AccountPoolSettingsChange",
    "AccountPoolSettingsHistoryEntry",
    "AccountPoolSettingsPreview",
    "AccountPoolSettingsRepository",
    "AccountPoolSettingsUpdate",
    "AccountPoolSettingsView",
    "PostgresAccountPoolSettingsRepository",
    "settings_preview",
)
