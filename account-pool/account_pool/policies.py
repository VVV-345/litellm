"""本模块保存管理策略及版本，代理尚未实现的策略保持待接入状态。"""

from __future__ import annotations

from typing import Final, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from psycopg.types.json import Jsonb

from account_pool.repository import _connection


class RoutingPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: Literal["auto", "random", "priority", "quota", "plan", "expiry", "custom"] = "auto"
    priority: int = Field(default=0, ge=-10000, le=10000)
    weight: int = Field(default=1, ge=1, le=10000)
    is_backup: bool = False
    preferred_account_ids: tuple[UUID, ...] = ()
    session_affinity: bool = False
    session_affinity_ttl: int = Field(default=3600, ge=60, le=86400)
    quota_reserve_percent: int = Field(default=0, ge=0, le=100)
    quota_snapshot_max_age: int = Field(default=300, ge=10, le=86400)
    max_attempts: int = Field(default=1, ge=1, le=5)
    retryable_statuses: tuple[int, ...] = (429, 502, 503, 504)
    backoff_ms: int = Field(default=1000, ge=0, le=60000)
    fallback_enabled: bool = False

    @field_validator("retryable_statuses")
    @classmethod
    def safe_retry_statuses(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        if any(value not in (408, 429, 500, 502, 503, 504) for value in values):
            raise ValueError("retryable_statuses contains a non-retryable status")
        return tuple(dict.fromkeys(values))


class ModelAlias(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    alias: str = Field(min_length=1, max_length=256)
    target: str = Field(min_length=1, max_length=256)


class TransportPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    image_generation: Literal["inherit", "enabled", "disabled"] = "inherit"
    websocket: Literal["inherit", "enabled", "disabled"] = "inherit"
    request_timeout_seconds: int = Field(default=120, ge=1, le=3600)
    debug_log_enabled: bool = False


class CodexPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    identity_fingerprint_mode: Literal["off", "device", "session", "full"] = "off"
    cli_only: bool = False
    allow_app_server: bool = False
    allow_app_server_clients: tuple[str, ...] = ()
    responses_compact_enabled: bool = False
    compact_ui: Literal["inherit", "enabled", "disabled"] = "inherit"
    model_context_window: int | None = Field(default=None, ge=1, le=10000000)
    model_auto_compact_token_limit: int | None = Field(default=None, ge=1, le=10000000)
    experimental_context_management: bool = False

    @model_validator(mode="after")
    def compact_limit_within_context(self) -> CodexPolicy:
        if self.model_context_window and self.model_auto_compact_token_limit:
            if self.model_auto_compact_token_limit > self.model_context_window:
                raise ValueError("Compact token limit exceeds the context window")
        return self


class AccountPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tags: tuple[str, ...] = Field(default=(), max_length=20)
    group: str = Field(default="", max_length=80)
    routing: RoutingPolicy = Field(default_factory=RoutingPolicy)
    excluded_models: tuple[str, ...] = Field(default=(), max_length=500)
    model_aliases: tuple[ModelAlias, ...] = Field(default=(), max_length=500)
    transport: TransportPolicy = Field(default_factory=TransportPolicy)
    codex: CodexPolicy | None = None

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() or len(value.strip()) > 40 for value in values):
            raise ValueError("Tags must contain between 1 and 40 characters")
        return tuple(dict.fromkeys(value.strip() for value in values))

    @model_validator(mode="after")
    def unique_aliases(self) -> AccountPolicy:
        if len({item.alias for item in self.model_aliases}) != len(self.model_aliases):
            raise ValueError("Model aliases must be unique")
        return self


class PolicyUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=0)
    policy: AccountPolicy


class PolicyView(BaseModel):
    model_config = ConfigDict(frozen=True)

    card_id: UUID
    version: int = 0
    policy: AccountPolicy = Field(default_factory=AccountPolicy)
    runtime_status: Literal["not_connected"] = "not_connected"
    metadata_status: Literal["saved"] = "saved"


class PolicyRepository(Protocol):
    async def list(self) -> tuple[PolicyView, ...]: ...

    async def get(self, card_id: UUID) -> PolicyView: ...

    async def save(self, card_id: UUID, request: PolicyUpdate) -> PolicyView | None: ...


class PostgresPolicyRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url: Final = database_url

    async def initialize(self) -> None:
        async with _connection(self._database_url) as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS account_pool_policies (
                    card_id uuid PRIMARY KEY REFERENCES account_pool_environments(id) ON DELETE CASCADE,
                    version integer NOT NULL,
                    policy jsonb NOT NULL
                )
                """
            )

    async def get(self, card_id: UUID) -> PolicyView:
        async with _connection(self._database_url) as connection:
            cursor: Final = await connection.execute("SELECT * FROM account_pool_policies WHERE card_id = %s", (card_id,))
            row: Final = await cursor.fetchone()
        return PolicyView(card_id=card_id) if row is None else PolicyView.model_validate(row)

    async def list(self) -> tuple[PolicyView, ...]:
        async with _connection(self._database_url) as connection:
            cursor: Final = await connection.execute("SELECT * FROM account_pool_policies ORDER BY card_id")
            rows: Final = await cursor.fetchall()
        return tuple(PolicyView.model_validate(row) for row in rows)

    async def save(self, card_id: UUID, request: PolicyUpdate) -> PolicyView | None:
        async with _connection(self._database_url) as connection:
            card: Final = await connection.execute(
                "SELECT id FROM account_pool_environments WHERE id = %s "
                "AND payload->>'status' <> 'deleting' FOR UPDATE", (card_id,),
            )
            if await card.fetchone() is None:
                return None
            saved: Final = await connection.execute(
                """
                INSERT INTO account_pool_policies (card_id, version, policy)
                SELECT %s, 1, %s WHERE %s = 0
                ON CONFLICT (card_id) DO NOTHING
                RETURNING *
                """, (card_id, Jsonb(request.policy.model_dump(mode="json")), request.version),
            ) if request.version == 0 else await connection.execute(
                "UPDATE account_pool_policies SET version = version + 1, policy = %s "
                "WHERE card_id = %s AND version = %s RETURNING *",
                (Jsonb(request.policy.model_dump(mode="json")), card_id, request.version),
            )
            row: Final = await saved.fetchone()
        return None if row is None else PolicyView.model_validate(row)
