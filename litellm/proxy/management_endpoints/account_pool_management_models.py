"""本模块验证号池管理协议的公开字段，与 Manager 保持契约一致。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final, Literal, TypeAlias
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    JsonValue,
    TypeAdapter,
    field_validator,
    model_validator,
)

ChannelKind = Literal["openai_compatible", "cliproxyapi"]
SupplierKind = Literal[
    "openai_compatible",
    "openai_codex",
    "anthropic_claude",
    "google_antigravity",
    "kimi",
    "xai",
    "gemini",
    "gemini_interactions",
    "vertex",
]
# 历史日志是审计数据，退役渠道值必须保持可读，但不能重新加入可配置渠道类型。
LogChannelKind: TypeAlias = ChannelKind | Literal["freebuff2api"]
LogSupplierKind: TypeAlias = SupplierKind | Literal["freebuff"]
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
    "plan",
    "expiry",
    "random_weighted",
    "custom_order",
    "backup_account",
    "concurrency_fallback",
    "token_budget_fallback",
    "retry_failover",
    "same_account_retry",
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
    identity_confuse: bool = False
    disable_codex_cloaking: bool = False

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_desktop_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        policy: Final = TypeAdapter(dict[str, object]).validate_python(value)
        legacy: Final = frozenset(
            (
                "compact_ui",
                "model_context_window",
                "model_auto_compact_token_limit",
                "experimental_context_management",
            )
        )
        return {key: item for key, item in policy.items() if key not in legacy}


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
        cloak_mode: Final = value.get(
            "cloak_mode", "always" if legacy_cloak is True else "never" if legacy_cloak is False else "auto"
        )
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
    headers: dict[str, str] = Field(default_factory=dict)
    from_protocol: str = Field(default="", max_length=80, alias="from-protocol")
    match: tuple[dict[str, object], ...] = Field(default=())
    not_match: tuple[dict[str, object], ...] = Field(default=(), alias="not-match")
    exist: tuple[str, ...] = Field(default=())
    not_exist: tuple[str, ...] = Field(default=(), alias="not-exist")


class PayloadRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    models: tuple[PayloadModelRule, ...] = Field(default=(), max_length=100)
    params: dict[str, object] = Field(default_factory=dict)


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
    default_concurrency_limit: int = Field(default=1, ge=0, le=1000)
    default_model_discovery: bool = True


class AccessSettingsValues(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    oauth_excluded_models: tuple[str, ...] = Field(default=(), max_length=500)
    oauth_model_aliases: dict[str, tuple[tuple[str, str], ...]] = Field(default_factory=dict)
    oauth_request_scoped_errors: dict[str, tuple[OAuthRequestScopedErrorRule, ...]] = Field(default_factory=dict)


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
    default_concurrency_limit: int = Field(default=1, ge=0, le=1000)
    default_model_discovery: bool = True
    default_proxy_profile_id: str | None = Field(default=None, max_length=120)
    max_attempts: int = Field(default=1, ge=1, le=5)
    request_timeout_seconds: int = Field(default=120, ge=1, le=3600)
    quota_refresh_interval_minutes: Literal[5, 15, 30, 60] = 5
    auth_refresh_interval_minutes: Literal[5, 15, 30, 60] = 15
    full_logging_enabled: bool = False
    daily_log_retention_days: int = Field(default=30, ge=1, le=3650)
    full_log_retention_days: int = Field(default=30, ge=1, le=3650)
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
    oauth_request_scoped_errors: dict[str, tuple[OAuthRequestScopedErrorRule, ...]] = Field(default_factory=dict)
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
    account_email: str | None = None
    account_id: str | None = None
    file_name: str | None = None
    last_error: str | None = None


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
        "debug",
        "provider_settings",
    ]
    status: Literal["gateway", "unsupported", "metadata"]


def policy_capabilities(supplier: str | None = None) -> tuple[PolicyCapability, ...]:
    provider_status: Final = "gateway"
    return (
        *(
            PolicyCapability(name=name, status="gateway")
            for name in ("routing", "models", "quota", "retry", "timeout", "client", "responses_compact", "image")
        ),
        PolicyCapability(name="identity", status="metadata" if supplier == "openai_codex" else "unsupported"),
        PolicyCapability(name="plan_expiry", status="gateway"),
        PolicyCapability(name="websocket", status="gateway"),
        PolicyCapability(name="debug", status="gateway"),
        PolicyCapability(name="provider_settings", status=provider_status),
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


class UpstreamSyncReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    state: Literal["idle", "queued", "running", "conflict", "failed", "passed", "promoted"] = "idle"
    action: Literal["none", "analyze", "promote"] = "none"
    request_id: UUID | None = None
    session_id: str | None = Field(default=None, max_length=128)
    target_tag: str | None = None
    base_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    candidate_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    conflict_files: tuple[str, ...] = ()
    failed_steps: tuple[str, ...] = ()
    message: str = ""
    workflow_url: HttpUrl | None = None
    updated_at: AwareDatetime | None = None


class UpstreamSyncView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    upstream_repository: str
    fork_repository: str
    sync_branch: str
    current_tag: str
    latest_tag: str
    latest_release_url: HttpUrl
    update_available: bool
    dispatch_configured: bool
    report: UpstreamSyncReport


class UpstreamSyncDispatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: UUID
    action: Literal["analyze", "promote"]
    target_tag: str
    state: Literal["queued"] = "queued"


class CodexReviewPackage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    filename: str
    branch: str
    target_tag: str | None = None
    content: str


class ErrorLogRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    occurred_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: AwareDatetime | None = None
    channel: LogChannelKind
    supplier: LogSupplierKind
    card_id: UUID
    environment_id: UUID
    account_id: UUID
    card_key_id: UUID | None = None
    request_id: UUID = Field(default_factory=uuid4)
    upstream_request_id: str | None = Field(default=None, max_length=256)
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
    cache_read_input_tokens: int | None = Field(default=None, ge=0)
    cache_creation_input_tokens: int | None = Field(default=None, ge=0)
    cache_rate: float | None = Field(default=None, ge=0, le=1)
    routing_reason: RoutingReason | None = None
    cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    session_id: str | None = Field(default=None, max_length=128)
    proxy_endpoint: str | None = Field(default=None, max_length=256)
    cost_source: str = "unknown"
    cost_details: dict[str, JsonValue] = Field(default_factory=dict)
    full_log_state: Literal["disabled", "stored", "truncated", "failed"] = "disabled"
    spend_sync_state: Literal["pending", "synced", "failed", "unavailable"] = "pending"
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
    session_id: str | None = Field(default=None, max_length=128)
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
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_rate: float | None = Field(default=None, ge=0, le=1)
    known_cost_requests: int = 0
    total_cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    average_duration_ms: float | None = None
    recent_errors: tuple[ErrorLogRecord, ...] = ()


class AccountPoolLogStorageStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    backend: Literal["postgresql"] = "postgresql"
    location: str
    row_count: int = Field(ge=0)
    allocated_bytes: int = Field(ge=0)


class ErrorLogDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    event: ErrorLogRecord
    attempts: tuple[ErrorLogRecord, ...]
    has_more: bool
