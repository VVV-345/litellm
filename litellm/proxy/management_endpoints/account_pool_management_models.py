"""本模块验证号池管理协议的公开字段，与 Manager 保持契约一致。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

ChannelKind = Literal["openai_compatible", "cliproxyapi", "freebuff2api"]
SupplierKind = Literal[
    "openai_compatible",
    "openai_codex",
    "anthropic_claude",
    "google_antigravity",
    "kimi",
    "xai",
    "freebuff",
]
LogStage = Literal[
    "provisioning",
    "authorization",
    "validation",
    "configuration",
    "quota",
    "cleanup",
    "authentication",
    "routing",
    "connection",
    "upstream",
    "response",
    "card_key",
]
ErrorCategory = Literal[
    "authentication",
    "authorization",
    "rate_limit",
    "timeout",
    "connection",
    "invalid_request",
    "upstream",
    "configuration",
    "unknown",
]
RoutingReason = Literal[
    "automatic",
    "single_account",
    "session_affinity",
    "session_rebind",
    "preferred_account",
    "priority",
    "quota",
    "random_weighted",
    "custom_order",
    "backup_account",
    "concurrency_fallback",
    "token_budget_fallback",
    "retry_failover",
]

BatchAction = Literal["refresh", "authorize", "enable", "disable", "cooldown", "release", "policy", "delete"]


class BatchTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    account_id: UUID
    version: int = Field(ge=0)
    policy_version: int = Field(default=0, ge=0)


class BatchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    job_id: UUID
    action: BatchAction
    targets: tuple[BatchTarget, ...] = Field(min_length=1, max_length=100)
    policy: AccountPolicy | None = None

    @model_validator(mode="after")
    def valid_action(self) -> BatchRequest:
        if len({target.account_id for target in self.targets}) != len(self.targets):
            raise ValueError("Duplicate accounts are not allowed")
        if (self.action == "policy") != (self.policy is not None):
            raise ValueError("Policy is required only for policy jobs")
        return self


class BatchAuthorization(BaseModel):
    model_config = ConfigDict(frozen=True)
    flow: Literal["browser_oauth", "device_code"]
    authorization_url: HttpUrl
    ssh_command: str | None
    user_code: str | None
    expires_at: AwareDatetime


class BatchItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    account_id: UUID
    status: Literal["queued", "running", "succeeded", "failed"]
    attempts: int = 0
    message: str | None = None
    authorization: BatchAuthorization | None = None
    finished_at: AwareDatetime | None = None


class BatchJob(BaseModel):
    model_config = ConfigDict(frozen=True)
    job_id: UUID
    action: BatchAction
    created_at: AwareDatetime
    items: tuple[BatchItem, ...]


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


class StreamingRule(BaseModel):
    """流式传输规则，将一个规则绑定到若干号池卡片。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    mode: Literal["enabled", "disabled"] = "enabled"
    card_ids: tuple[UUID, ...] = Field(default=(), max_length=100)


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
    oauth_model_aliases: dict[str, tuple[tuple[str, str], ...]] = Field(default_factory=dict)
    oauth_request_scoped_errors: bool = False
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


class AccountPoolSettingsRollbackRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_version: int = Field(ge=0)
    target_version: int = Field(ge=0)


class AccountPoolCredential(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    card_id: UUID
    card_name: str
    supplier: str
    kind: str
    status: str
    enabled: bool
    model_count: int = Field(default=0, ge=0)
    auth_index: str | None = None


class AccountPoolCredentialRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=0)
    api_key: str = Field(min_length=1, max_length=4096, repr=False)
    proxy_profile_id: str | None = Field(default=None, max_length=120)
    weight: int = Field(default=1, ge=1, le=10000)


class AccountPoolCredentialDeleteRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=0)
    credential_index: int = Field(ge=0, le=99)


class AccountPoolCredentialMutationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    version: int = Field(ge=0)


class AccountPoolLogClearResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    deleted: int = Field(ge=0)


class AccountPoolPluginManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    plugin_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    display_name: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=64)
    runtime: Literal["sidecar"] = "sidecar"
    entrypoint: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    capabilities: tuple[str, ...] = ()
    provider_families: tuple[str, ...] = ()


class AccountPoolPluginRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    manifest: AccountPoolPluginManifest
    state: Literal["installed", "enabled", "disabled", "incompatible", "error"]
    installed_at: datetime
    updated_at: datetime
    last_error: str | None = None


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


def policy_capabilities(supplier: str | None = None) -> tuple[PolicyCapability, ...]:
    provider_status: Final = "metadata"
    return (
        *(
            PolicyCapability(name=name, status="gateway")
            for name in ("routing", "models", "quota", "retry", "timeout", "client", "responses_compact", "image")
        ),
        PolicyCapability(name="identity", status="metadata" if supplier == "openai_codex" else "unsupported"),
        *(PolicyCapability(name=name, status="unsupported") for name in ("websocket", "plan_expiry", "debug")),
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


BatchRequest.model_rebuild()


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
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    routing_reason: RoutingReason | None = None
    cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
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


class ErrorStats(BaseModel):
    model_config = ConfigDict(frozen=True)
    card_id: UUID | None = None
    account_id: UUID | None = None
    model: str | None = None
    total_requests: int = 0
    succeeded_requests: int = 0
    failed_requests: int = 0
    retried_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    known_cost_requests: int = 0
    total_cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    average_duration_ms: float | None = None
    recent_errors: tuple[ErrorLogRecord, ...] = ()


class ErrorLogDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    event: ErrorLogRecord
    attempts: tuple[ErrorLogRecord, ...]
    has_more: bool
