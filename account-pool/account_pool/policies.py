"""本模块保存管理策略及版本，代理尚未实现的策略保持待接入状态。"""

from __future__ import annotations

import asyncio
from datetime import datetime
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
    identity_confuse: bool = False
    disable_codex_cloaking: bool = False

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_desktop_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        legacy: Final = frozenset(
            (
                "compact_ui",
                "model_context_window",
                "model_auto_compact_token_limit",
                "experimental_context_management",
            )
        )
        return {key: item for key, item in value.items() if key not in legacy}


class ClaudePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fingerprint_profile: Literal["inherit", "claude-code-cli"] = "inherit"
    cloak_mode: Literal["auto", "always", "never"] = "auto"
    rebuild_mid_system_message: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        fingerprint: Final = value.get("fingerprint_profile", "inherit")
        normalized_fingerprint: Final = (
            "claude-code-cli" if fingerprint in ("claude-code-cli", "oauth-cli") else "inherit"
        )
        legacy_cloak: Final = value.get("cloak")
        cloak_mode: Final = value.get("cloak_mode", "always" if legacy_cloak is True else "never" if legacy_cloak is False else "auto")
        return {
            key: item
            for key, item in {**value, "fingerprint_profile": normalized_fingerprint, "cloak_mode": cloak_mode}.items()
            if key not in {"experimental_cch_signing", "cloak"}
        }


class KimiPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fingerprint_profile: Literal["inherit", "claude-code-cli"] = "inherit"


class XaiPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    inject_x_search: bool = False


class AntigravityPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sensitive_words: tuple[str, ...] = Field(default=(), max_length=100)
    signature_cache_enabled: bool = True
    signature_bypass_strict: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        legacy_cache: Final = value.get("signature_cache")
        return {
            key: item
            for key, item in {
                **value,
                "sensitive_words": value.get("sensitive_words", ()),
                "signature_cache_enabled": value.get("signature_cache_enabled", legacy_cache != "disabled"),
                "signature_bypass_strict": value.get(
                    "signature_bypass_strict", value.get("strict_bypass_signature", False)
                ),
            }.items()
            if key not in {"sensitive_word_filter", "signature_cache", "strict_bypass_signature"}
        }

    @field_validator("sensitive_words")
    @classmethod
    def normalize_sensitive_words(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


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
    kimi: KimiPolicy | None = None
    xai: XaiPolicy | None = None
    antigravity: AntigravityPolicy | None = None

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_provider_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        return {key: item for key, item in value.items() if key != "openai_compatible"}

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
    provider_status: Final = "gateway"
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
    runtime_status: Literal["partial", "synced", "failed"] = "partial"
    runtime_error: str | None = None
    runtime_updated_at: datetime | None = None
    capabilities: tuple[PolicyCapability, ...] = Field(default_factory=policy_capabilities)
    metadata_status: Literal["saved"] = "saved"


class PolicyRepository(Protocol):
    async def list(self) -> tuple[PolicyView, ...]: ...

    async def get(self, card_id: UUID) -> PolicyView: ...

    async def save(self, card_id: UUID, request: PolicyUpdate) -> PolicyView | None: ...

    async def set_runtime_status(
        self,
        card_id: UUID,
        version: int,
        status: Literal["partial", "synced", "failed"],
        error: str | None = None,
    ) -> PolicyView | None: ...


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
        (SupplierKind.KIMI, policy.kimi),
        (SupplierKind.XAI, policy.xai),
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
                    policy jsonb NOT NULL,
                    runtime_status text NOT NULL DEFAULT 'partial',
                    runtime_error text,
                    runtime_updated_at timestamptz
                )
                """
            )
            await connection.execute(
                "ALTER TABLE account_pool_policies ADD COLUMN IF NOT EXISTS runtime_status text NOT NULL DEFAULT 'partial'"
            )
            await connection.execute(
                "ALTER TABLE account_pool_policies ADD COLUMN IF NOT EXISTS runtime_error text"
            )
            await connection.execute(
                "ALTER TABLE account_pool_policies ADD COLUMN IF NOT EXISTS runtime_updated_at timestamptz"
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

    async def set_runtime_status(
        self,
        card_id: UUID,
        version: int,
        status: Literal["partial", "synced", "failed"],
        error: str | None = None,
    ) -> PolicyView | None:
        async with database_connection(self._database_url) as connection:
            cursor: Final = await connection.execute(
                "UPDATE account_pool_policies SET runtime_status = %s, runtime_error = %s, "
                "runtime_updated_at = CURRENT_TIMESTAMP WHERE card_id = %s AND version = %s RETURNING *",
                (status, error, card_id, version),
            )
            row: Final = await cursor.fetchone()
        return None if row is None else PolicyView.model_validate(row)
