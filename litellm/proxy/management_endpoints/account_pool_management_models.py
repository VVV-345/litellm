"""本模块验证号池管理协议的公开字段，与 Manager 保持契约一致。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

ChannelKind = Literal["cliproxyapi", "freebuff2api"]
SupplierKind = Literal["openai_codex", "anthropic_claude", "google_antigravity", "kimi", "xai", "freebuff"]
LogStage = Literal["provisioning", "authorization", "validation", "configuration", "quota", "cleanup", "authentication", "routing", "connection", "upstream", "response", "card_key"]
ErrorCategory = Literal["authentication", "authorization", "rate_limit", "timeout", "connection", "invalid_request", "upstream", "configuration", "unknown"]

class CardKeyStatus(BaseModel):
    model_config = ConfigDict(frozen=True)
    key_id: UUID
    card_id: UUID
    created_at: AwareDatetime
    revoked_at: AwareDatetime | None
    last_used_at: AwareDatetime | None


class CardKeyIssue(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: CardKeyStatus
    key: str


class CardKeyChange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    expected_key_id: UUID


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

class ErrorLogRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    occurred_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: AwareDatetime | None = None
    channel: ChannelKind
    supplier: SupplierKind
    card_id: UUID
    environment_id: UUID
    account_id: UUID
    card_key_id: UUID | None = None
    request_id: UUID = Field(default_factory=uuid4)
    trace_id: UUID | None = None
    attempt: int = Field(default=1, ge=1)
    operation: str = Field(max_length=160)
    stage: LogStage
    model: str | None = Field(default=None, max_length=256)
    endpoint: str | None = Field(default=None, max_length=256)
    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"] | None = None
    error_category: ErrorCategory | None = None
    severity: Literal["info", "warning", "error"] = "error"
    http_status: int | None = Field(default=None, ge=100, le=599)
    upstream_code: str | None = Field(default=None, max_length=120)
    retryable: bool = False
    retry_count: int = Field(default=0, ge=0)
    switched_account: bool = False
    next_account_id: UUID | None = None
    message: str
    detail: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    final_status: Literal["failed", "retrying", "succeeded"] = "failed"



class ErrorLogQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    occurred_from: AwareDatetime | None = None
    occurred_to: AwareDatetime | None = None
    channel: ChannelKind | None = None
    supplier: SupplierKind | None = None
    card_id: UUID | None = None
    environment_id: UUID | None = None
    account_id: UUID | None = None
    card_key_id: UUID | None = None
    request_id: UUID | None = None
    model: str | None = Field(default=None, max_length=256)
    stage: LogStage | None = None
    error_category: ErrorCategory | None = None
    retryable: bool | None = None
    switched_account: bool | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=100000)

    @model_validator(mode="after")
    def ordered_time_range(self) -> ErrorLogQuery:
        if self.occurred_from and self.occurred_to and self.occurred_from > self.occurred_to:
            raise ValueError("occurred_from must not be after occurred_to")
        return self

class ErrorLogPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[ErrorLogRecord, ...]
    has_more: bool

class ErrorLogDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    event: ErrorLogRecord
    attempts: tuple[ErrorLogRecord, ...]
    has_more: bool
