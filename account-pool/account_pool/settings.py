"""本模块保存号池全局默认配置及其版本历史，禁止存储密钥和凭据内容。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Final, Literal, Protocol, TypeVar
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


class CommonSettingsValues(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    default_route: Literal["auto", "priority", "random", "quota"] = "auto"
    default_concurrency_limit: int = Field(default=1, ge=1, le=1000)
    default_model_discovery: bool = True


class AccessSettingsValues(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    oauth_excluded_models: tuple[str, ...] = Field(default=(), max_length=500)
    oauth_model_aliases: Mapping[str, tuple[tuple[str, str], ...]] = Field(default_factory=dict)
    oauth_request_scoped_errors: Mapping[str, tuple[OAuthRequestScopedErrorRule, ...]] = Field(default_factory=dict)


class NetworkSettingsValues(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    default_proxy_profile_id: str | None = Field(default=None, max_length=120)
    max_attempts: int = Field(default=1, ge=1, le=5)
    request_timeout_seconds: int = Field(default=120, ge=1, le=3600)
    websocket_enabled: bool = False


class QuotaSettingsValues(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    quota_switch_project: bool = False
    quota_switch_preview_model: bool = False


class StreamingSettingsValues(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True


class AdvancedSettingsValues(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    plugins_enabled: bool = False
    websocket_auth_enabled: bool = False
    force_model_prefix: bool = False
    request_retry: int = Field(default=1, ge=0, le=20)
    max_retry_credentials: int = Field(default=1, ge=0, le=100)
    max_retry_interval: int = Field(default=0, ge=0, le=3600)


class CommonSettingsProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    card_ids: tuple[UUID, ...] = Field(default=(), max_length=100)
    inherit_global: bool = True
    values: CommonSettingsValues = Field(default_factory=CommonSettingsValues)


class AccessSettingsProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    card_ids: tuple[UUID, ...] = Field(default=(), max_length=100)
    inherit_global: bool = True
    values: AccessSettingsValues = Field(default_factory=AccessSettingsValues)


class NetworkSettingsProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    card_ids: tuple[UUID, ...] = Field(default=(), max_length=100)
    inherit_global: bool = True
    values: NetworkSettingsValues = Field(default_factory=NetworkSettingsValues)


class QuotaSettingsProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    card_ids: tuple[UUID, ...] = Field(default=(), max_length=100)
    inherit_global: bool = True
    values: QuotaSettingsValues = Field(default_factory=QuotaSettingsValues)


class StreamingSettingsProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    card_ids: tuple[UUID, ...] = Field(default=(), max_length=100)
    inherit_global: bool = True
    values: StreamingSettingsValues = Field(default_factory=StreamingSettingsValues)


class AdvancedSettingsProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    card_ids: tuple[UUID, ...] = Field(default=(), max_length=100)
    inherit_global: bool = True
    values: AdvancedSettingsValues = Field(default_factory=AdvancedSettingsValues)


class PayloadSettingsProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    card_ids: tuple[UUID, ...] = Field(default=(), max_length=100)
    inherit_global: bool = True
    values: PayloadSettings = Field(default_factory=PayloadSettings)


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
    streaming_enabled: bool = True
    common_profiles: tuple[CommonSettingsProfile, ...] = Field(default=(), max_length=100)
    access_profiles: tuple[AccessSettingsProfile, ...] = Field(default=(), max_length=100)
    network_profiles: tuple[NetworkSettingsProfile, ...] = Field(default=(), max_length=100)
    quota_profiles: tuple[QuotaSettingsProfile, ...] = Field(default=(), max_length=100)
    streaming_profiles: tuple[StreamingSettingsProfile, ...] = Field(default=(), max_length=100)
    advanced_profiles: tuple[AdvancedSettingsProfile, ...] = Field(default=(), max_length=100)
    payload_profiles: tuple[PayloadSettingsProfile, ...] = Field(default=(), max_length=100)
    streaming_rules: tuple[StreamingRule, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def unique_profile_cards(self) -> AccountPoolSettings:
        groups: Final = (
            ("common profiles", self.common_profiles),
            ("access profiles", self.access_profiles),
            ("network profiles", self.network_profiles),
            ("quota profiles", self.quota_profiles),
            ("streaming profiles", self.streaming_profiles),
            ("advanced profiles", self.advanced_profiles),
            ("payload profiles", self.payload_profiles),
            ("streaming rules", self.streaming_rules),
        )
        duplicate_category: Final = next(
            (
                category
                for category, profiles in groups
                if len(tuple(card_id for profile in profiles for card_id in profile.card_ids))
                != len(frozenset(card_id for profile in profiles for card_id in profile.card_ids))
            ),
            None,
        )
        if duplicate_category is not None:
            raise ValueError(f"A card cannot be assigned to multiple {duplicate_category}")
        return self


SettingsProfile = TypeVar(
    "SettingsProfile",
    CommonSettingsProfile,
    AccessSettingsProfile,
    NetworkSettingsProfile,
    QuotaSettingsProfile,
    StreamingSettingsProfile,
    AdvancedSettingsProfile,
    PayloadSettingsProfile,
)


def settings_for_card(settings: AccountPoolSettings, card_id: UUID) -> AccountPoolSettings:
    common: Final = _profile_for_card(settings.common_profiles, card_id)
    access: Final = _profile_for_card(settings.access_profiles, card_id)
    network: Final = _profile_for_card(settings.network_profiles, card_id)
    quota: Final = _profile_for_card(settings.quota_profiles, card_id)
    streaming: Final = _profile_for_card(settings.streaming_profiles, card_id)
    advanced: Final = _profile_for_card(settings.advanced_profiles, card_id)
    payload: Final = _profile_for_card(settings.payload_profiles, card_id)
    common_values: Final = (
        common.values
        if common is not None and not common.inherit_global
        else CommonSettingsValues(
            default_route=settings.default_route,
            default_concurrency_limit=settings.default_concurrency_limit,
            default_model_discovery=settings.default_model_discovery,
        )
    )
    access_values: Final = (
        access.values
        if access is not None and not access.inherit_global
        else AccessSettingsValues(
            oauth_excluded_models=settings.oauth_excluded_models,
            oauth_model_aliases=settings.oauth_model_aliases,
            oauth_request_scoped_errors=settings.oauth_request_scoped_errors,
        )
    )
    network_values: Final = (
        network.values
        if network is not None and not network.inherit_global
        else NetworkSettingsValues(
            default_proxy_profile_id=settings.default_proxy_profile_id,
            max_attempts=settings.max_attempts,
            request_timeout_seconds=settings.request_timeout_seconds,
            websocket_enabled=settings.websocket_enabled,
        )
    )
    quota_values: Final = (
        quota.values
        if quota is not None and not quota.inherit_global
        else QuotaSettingsValues(
            quota_switch_project=settings.quota_switch_project,
            quota_switch_preview_model=settings.quota_switch_preview_model,
        )
    )
    streaming_values: Final = (
        streaming.values
        if streaming is not None and not streaming.inherit_global
        else StreamingSettingsValues(enabled=settings.streaming_enabled)
    )
    advanced_values: Final = (
        advanced.values
        if advanced is not None and not advanced.inherit_global
        else AdvancedSettingsValues(
            plugins_enabled=settings.plugins_enabled,
            websocket_auth_enabled=settings.websocket_auth_enabled,
            force_model_prefix=settings.force_model_prefix,
            request_retry=settings.request_retry,
            max_retry_credentials=settings.max_retry_credentials,
            max_retry_interval=settings.max_retry_interval,
        )
    )
    payload_values: Final = payload.values if payload is not None and not payload.inherit_global else settings.payload
    return settings.model_copy(
        update={
            **common_values.model_dump(),
            **access_values.model_dump(),
            **network_values.model_dump(),
            **quota_values.model_dump(),
            **advanced_values.model_dump(),
            "streaming_enabled": streaming_values.enabled,
            "payload": payload_values,
        }
    )


def streaming_mode_for_card(
    settings: AccountPoolSettings,
    card_id: UUID,
) -> Literal["inherit", "enabled", "disabled"]:
    profile: Final = _profile_for_card(settings.streaming_profiles, card_id)
    if profile is not None:
        enabled: Final = settings.streaming_enabled if profile.inherit_global else profile.values.enabled
        return "enabled" if enabled else "disabled"
    legacy: Final = next((rule for rule in settings.streaming_rules if card_id in rule.card_ids), None)
    if legacy is not None:
        return legacy.mode
    return "inherit" if settings.streaming_enabled else "disabled"


def _profile_for_card(profiles: Sequence[SettingsProfile], card_id: UUID) -> SettingsProfile | None:
    return next((profile for profile in profiles if card_id in profile.card_ids), None)


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
    return any(previous_values[key] != proposed_values[key] for key in reload_keys) or _requires_reload(proposed)


__all__ = (
    "AccessSettingsProfile",
    "AccessSettingsValues",
    "AccountPoolSettings",
    "AccountPoolSettingsChange",
    "AccountPoolSettingsHistoryEntry",
    "AccountPoolSettingsPreview",
    "AccountPoolSettingsRepository",
    "AccountPoolSettingsUpdate",
    "AccountPoolSettingsView",
    "AdvancedSettingsProfile",
    "AdvancedSettingsValues",
    "CommonSettingsProfile",
    "CommonSettingsValues",
    "NetworkSettingsProfile",
    "NetworkSettingsValues",
    "PayloadSettingsProfile",
    "PostgresAccountPoolSettingsRepository",
    "QuotaSettingsProfile",
    "QuotaSettingsValues",
    "StreamingSettingsProfile",
    "StreamingSettingsValues",
    "settings_for_card",
    "settings_preview",
    "streaming_mode_for_card",
)
