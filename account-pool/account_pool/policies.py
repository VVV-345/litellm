"""本模块保存管理策略及版本，代理尚未实现的策略保持待接入状态。"""

from __future__ import annotations

import asyncio
from typing import Final, Literal, Protocol
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from account_pool.domain import EnvironmentRecord, SupplierKind
from account_pool.repository import database_connection


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
    token_budget_limit: int | None = Field(default=None, ge=1, le=1000000000000)
    token_budget_window_seconds: int = Field(default=3600, ge=60, le=2592000)
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
    identity_confuse: bool = False
    disable_codex_cloaking: bool = False

    @model_validator(mode="after")
    def compact_limit_within_context(self) -> CodexPolicy:
        if self.model_context_window and self.model_auto_compact_token_limit:
            if self.model_auto_compact_token_limit > self.model_context_window:
                raise ValueError("Compact token limit exceeds the context window")
        return self


class ClaudePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fingerprint_profile: Literal["inherit", "disabled", "claude-code-cli", "oauth-cli"] = "inherit"
    experimental_cch_signing: bool = False
    cloak: bool = False
    rebuild_mid_system_message: bool = False


class XaiPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    inject_x_search: bool = False


class OpenAICompatiblePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    support_prompt_cache_key: bool = False


class AntigravityPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sensitive_word_filter: Literal["inherit", "enabled", "disabled"] = "inherit"
    signature_cache: Literal["inherit", "enabled", "disabled"] = "inherit"
    strict_bypass_signature: bool = False


class AccountPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tags: tuple[str, ...] = Field(default=(), max_length=20)
    group: str = Field(default="", max_length=80)
    account_ids: tuple[UUID, ...] = Field(default=(), max_length=100)
    routing: RoutingPolicy = Field(default_factory=RoutingPolicy)
    excluded_models: tuple[str, ...] = Field(default=(), max_length=500)
    model_aliases: tuple[ModelAlias, ...] = Field(default=(), max_length=500)
    transport: TransportPolicy = Field(default_factory=TransportPolicy)
    codex: CodexPolicy | None = None
    claude: ClaudePolicy | None = None
    xai: XaiPolicy | None = None
    openai_compatible: OpenAICompatiblePolicy | None = None
    antigravity: AntigravityPolicy | None = None

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


class PolicyCapability(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: Literal[
        "routing",
        "models",
        "quota",
        "retry",
        "timeout",
        "client",
        "responses_compact",
        "image",
        "identity",
        "websocket",
        "plan_expiry",
        "desktop_compact",
        "debug",
        "provider_settings",
    ]
    status: Literal["gateway", "unsupported", "desktop", "metadata"]


def policy_capabilities(supplier: SupplierKind | None = None) -> tuple[PolicyCapability, ...]:
    provider_status: Final = "metadata"
    return (
        *(
            PolicyCapability(name=name, status="gateway")
            for name in ("routing", "models", "quota", "retry", "timeout", "client", "responses_compact", "image")
        ),
        PolicyCapability(name="identity", status="metadata" if supplier is SupplierKind.OPENAI_CODEX else "unsupported"),
        *(
            PolicyCapability(name=name, status="unsupported")
            for name in ("websocket", "plan_expiry", "debug")
        ),
        PolicyCapability(name="provider_settings", status=provider_status),
        PolicyCapability(name="desktop_compact", status="desktop"),
    )


class PolicyView(BaseModel):
    model_config = ConfigDict(frozen=True)

    card_id: UUID
    version: int = 0
    policy: AccountPolicy = Field(default_factory=AccountPolicy)
    runtime_status: Literal["partial"] = "partial"
    capabilities: tuple[PolicyCapability, ...] = Field(default_factory=policy_capabilities)
    metadata_status: Literal["saved"] = "saved"


class PolicyRepository(Protocol):
    async def list(self) -> tuple[PolicyView, ...]: ...

    async def get(self, card_id: UUID) -> PolicyView: ...

    async def save(self, card_id: UUID, request: PolicyUpdate) -> PolicyView | None: ...


class PolicyEnvironmentRepository(Protocol):
    async def get(self, environment_id: UUID) -> EnvironmentRecord | None: ...


async def policy_validation_error(
    card: EnvironmentRecord,
    policy: AccountPolicy,
    environments: PolicyEnvironmentRepository,
) -> str | None:
    if policy.codex is not None and card.supplier is not SupplierKind.OPENAI_CODEX:
        return "Codex settings apply only to Codex accounts"
    provider_policies: Final = (
        (SupplierKind.ANTHROPIC_CLAUDE, policy.claude),
        (SupplierKind.XAI, policy.xai),
        (SupplierKind.OPENAI_COMPATIBLE, policy.openai_compatible),
        (SupplierKind.GOOGLE_ANTIGRAVITY, policy.antigravity),
    )
    if any(value is not None and card.supplier is not supplier for supplier, value in provider_policies):
        return "Provider-specific settings must match the card supplier"
    resolved: Final = await asyncio.gather(*(environments.get(identifier) for identifier in policy.account_ids))
    if any(member is None for member in resolved):
        return "Bound accounts must exist and use the same channel and supplier"
    members: Final = tuple(member for member in resolved if member is not None)
    if any(member.channel != card.channel or member.supplier != card.supplier for member in members):
        return "Bound accounts must exist and use the same channel and supplier"
    scope: Final = frozenset((card.id, *policy.account_ids))
    if not frozenset(policy.routing.preferred_account_ids).issubset(scope):
        return "Preferred accounts must be bound to this card"
    available: Final = frozenset(model for environment in (card, *members) for model in environment.available_models)
    if any(alias.target not in available for alias in policy.model_aliases):
        return "Model aliases contain unavailable targets"
    return None


class PostgresPolicyRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url: Final = database_url

    async def initialize(self) -> None:
        async with database_connection(self._database_url) as connection:
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
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "SELECT * FROM account_pool_policies WHERE card_id = %s", (card_id,)
            )
            row: Final = await cursor.fetchone()
        return PolicyView(card_id=card_id) if row is None else PolicyView.model_validate(row)

    async def list(self) -> tuple[PolicyView, ...]:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute("SELECT * FROM account_pool_policies ORDER BY card_id")
            rows: Final = await cursor.fetchall()
        return tuple(PolicyView.model_validate(row) for row in rows)

    async def save(self, card_id: UUID, request: PolicyUpdate) -> PolicyView | None:
        async with database_connection(self._database_url) as connection:
            card: Final = await connection.execute(
                "SELECT id FROM account_pool_environments WHERE id = %s "
                "AND payload->>'status' <> 'deleting' FOR UPDATE",
                (card_id,),
            )
            if await card.fetchone() is None:
                return None
            saved: Final = (
                await connection.execute(
                    """
                INSERT INTO account_pool_policies (card_id, version, policy)
                SELECT %s, 1, %s WHERE %s = 0
                ON CONFLICT (card_id) DO NOTHING
                RETURNING *
                """,
                    (card_id, Jsonb(request.policy.model_dump(mode="json")), request.version),
                )
                if request.version == 0
                else await connection.execute(
                    "UPDATE account_pool_policies SET version = version + 1, policy = %s "
                    "WHERE card_id = %s AND version = %s RETURNING *",
                    (Jsonb(request.policy.model_dump(mode="json")), card_id, request.version),
                )
            )
            row: Final = await saved.fetchone()
        return None if row is None else PolicyView.model_validate(row)
