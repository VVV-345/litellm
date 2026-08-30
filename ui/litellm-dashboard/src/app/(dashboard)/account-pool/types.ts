// 本文件定义 Account Pool Dashboard 使用的脱敏渠道、解析任务和解析结果类型。

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type ChannelPriority = 100 | 200 | 300 | 400;
export type RoutingStrategy =
  | "priority"
  | "random"
  | "lowest_latency"
  | "highest_remaining_quota"
  | "lowest_effective_cost"
  | "least_inflight"
  | "weighted_round_robin"
  | "quota_aware_least_inflight";

export interface ChannelSummary {
  channel_id: string;
  display_name: string;
  provider: string;
  model_discovery_provider_id?: string | null;
  parser_provider_id?: string | null;
  group: string | null;
  base_url_display: string;
  administrative_state: "enabled" | "paused" | "disabled" | "pending_delete";
  max_concurrency: number;
  priority: ChannelPriority;
  weight: number;
  key_mask: string | null;
  binding_count: number;
  enabled_binding_count: number;
  models: string[];
  created_at: string;
  updated_at: string;
}

export interface ChannelListResponse {
  channels: ChannelSummary[];
}

export type AdministrativeState = ChannelSummary["administrative_state"];
export type BindingOwnership = "pool_managed" | "externally_managed";
export type DeleteMode = "detach_only" | "delete_managed_deployment";
export type OperationStatus = "pending_create" | "pending_update" | "pending_delete" | "applied" | "failed";

export interface QuotaConfig {
  unit: "tokens" | "usd";
  total: number | null;
  five_hour: number | null;
  weekly: number | null;
}

export interface ChannelBindingInput {
  binding_id: string | null;
  public_model: string;
  provider_model: string | null;
  litellm_deployment_id: string | null;
  ownership: BindingOwnership;
  enabled: boolean;
}

export interface ChannelMutationRequest {
  display_name: string;
  provider: string;
  model_discovery_provider_id: string | null;
  group: string | null;
  base_url_display: string;
  administrative_state: AdministrativeState;
  max_concurrency: number;
  priority: ChannelPriority;
  weight: number;
  quotas: QuotaConfig;
  api_key: string | null;
  bindings: ChannelBindingInput[];
}

export interface ChannelDetail extends Omit<ChannelMutationRequest, "api_key"> {
  channel_id: string;
  parser_provider_id?: string | null;
  key_mask: string | null;
}

export interface SafeOperationFailure {
  code: string;
  message: string;
}

export interface ChannelOperation {
  status: "accepted" | "existing";
  operation_id: string;
  channel_id: string;
  operation_status: OperationStatus;
  requires_key: boolean;
  failure: SafeOperationFailure | null;
}

export interface HealthProbeResult {
  probe_id: string;
  status: "succeeded" | "failed" | "skipped";
  trigger: "manual" | "initial" | "half_open" | "idle";
  channel_id: string | null;
  account_id: string | null;
  deployment_id: string | null;
  public_model: string | null;
  reason_code: string | null;
  response_status_code: number | null;
  latency_ms: number;
}

export type HealthTransition = "success" | "disable" | "cooldown" | "observe" | "transient_failure";

export interface HealthRuntimeSnapshot {
  account_id: string;
  enabled: boolean;
  health: "unknown" | "healthy" | "degraded" | "unhealthy" | "half_open" | "cooldown" | "disabled";
  inflight: number;
  max_concurrency: number;
  cooldown_until: number | null;
  consecutive_failures: number;
  reason_code: string | null;
  quota: QuotaConfig;
}

export interface HealthExclusion {
  scope: "channel" | "model" | "deployment" | "billing_route";
  source: "health" | "restriction" | "capacity";
  account_id: string;
  model: string | null;
  deployment_id: string | null;
  billing_route_id: string | null;
  reason_code: string;
  starts_at: number;
  retry_at: number | null;
  state: "active" | "half_open" | "cleared";
}

export interface HealthActivity {
  channel_id: string | null;
  account_id: string;
  model_id: string;
  deployment_id: string;
  last_request_at: string | null;
  last_success_at: string | null;
  last_failure_at: string | null;
  last_probe_at: string | null;
  last_probe_success_at: string | null;
  last_probe_failure_at: string | null;
  updated_at: string;
}

export interface HealthEventRecord {
  event: {
    event_id: string;
    event_type: "passive_health_result" | "active_health_probe_result";
    occurred_at: string;
    channel_id: string | null;
    model_id: string;
    deployment_id: string;
    request_id: string | null;
    lease_id: string | null;
    reason_code: string | null;
    actor_type: "system";
    actor_id: string;
    safe_details: {
      kind: "passive_health_result" | "active_health_probe_result";
      outcome: "succeeded" | "failed";
      transition: HealthTransition;
      trigger?: "manual" | "initial" | "half_open" | "idle";
      response_status_code: number | null;
      latency_ms: number | null;
    };
  };
  health: {
    event_id: string;
    account_id: string;
    source: "passive_request" | "active_probe";
    outcome: "succeeded" | "failed";
    transition: HealthTransition;
    scope: HealthExclusion["scope"];
    retry_at: string | null;
    probe_trigger: "manual" | "initial" | "half_open" | "idle" | null;
  };
}

export interface ChannelHealthDetail {
  channel_id: string;
  account_id: string;
  runtime: HealthRuntimeSnapshot;
  exclusions: HealthExclusion[];
  activities: HealthActivity[];
  events: HealthEventRecord[];
  persistence_available: boolean;
}

export interface ParsedChannelData {
  subscription: JsonValue;
  metered: JsonValue;
  billing_routes: JsonValue[];
  capabilities: string[];
  unresolved_fields: JsonValue[];
  evidence: JsonValue[];
  warnings: string[];
}

export interface ActiveOverride {
  override_id: string;
  field_path: string;
  source_parser_run_id: string;
  occurred_at: string;
}

export interface EffectiveParserData {
  status: "loaded";
  channel_id: string;
  parser_run_id: string;
  parser_id: string;
  parser_version: string;
  parsed_at: string;
  parser_status: string;
  raw_result: ParsedChannelData;
  effective_result: ParsedChannelData;
  active_overrides: ActiveOverride[];
  applied_override_ids: string[];
  override_failures: JsonValue[];
}

export interface ParserRunSummary {
  parser_run_id: string;
  parser_id: string;
  parser_version: string;
  parsed_at: string;
  status: string;
  discovered_models: string[];
  issues: JsonValue[];
  export: JsonValue;
}

export interface ParserRunHistory {
  status: "loaded";
  channel_id: string;
  runs: ParserRunSummary[];
}

export interface ProviderCapability {
  capability: string;
  state: "supported" | "unsupported" | "unavailable";
  message: string;
}

export interface ProviderServiceManifest {
  provider_id: string;
  display_name: string;
  default_api_base: string;
  litellm_provider_prefix: string;
  capabilities: ProviderCapability[];
}

export interface UpstreamProviderManifest {
  provider_id: string;
  display_name: string;
  default_api_base: string;
}

export interface UpstreamModelDiscoveryRequest {
  provider_id: string;
  upstream_url: string;
  api_key: string;
}

export interface UpstreamModelDiscoveryResult {
  ok: boolean;
  provider_id: string;
  normalized_api_base: string;
  message: string;
  failure_code: string | null;
  models: string[];
}

export interface ProviderValidationRequest {
  provider_id: string;
  api_base: string;
  api_key: string;
  group: string | null;
}

export interface ProviderValidationModel {
  model: string;
}

export interface ProviderValidationResult {
  ok: boolean;
  provider_id: string;
  normalized_api_base: string;
  group: string | null;
  key_fingerprint: string | null;
  message: string;
  failure_code: string | null;
  models: ProviderValidationModel[];
}

export interface ParserTaskAccepted {
  status: "accepted";
  task_id: string;
  channel_id: string;
  parser_run_id: string;
}

export interface ParserTaskRecord {
  task_id: string;
  channel_id: string;
  parser_run_id: string;
  provider_id: string;
  explicit_parser_id: string | null;
  openai_compatible: boolean;
  status: "running" | "completed" | "failed" | "interrupted_requires_key";
  created_at: string;
  heartbeat_at: string;
  completed_at: string | null;
  failure_code: string | null;
}

export interface ParserTaskView {
  status: "loaded";
  task: ParserTaskRecord;
}

export interface ParserTaskRequest {
  provider_id: string;
  api_key: string;
  group: string | null;
  explicit_parser_id: string | null;
  openai_compatible: boolean;
  username: string | null;
  password: string | null;
}

export interface OverrideTarget {
  kind: "root_field" | "subscription_field";
  field: string;
}

export interface OverrideSetRequest {
  override_id: string;
  target: OverrideTarget;
  value: JsonValue;
  expected_override_id: string | null;
  reason: string;
}

export interface OverrideRevokeRequest {
  override_id: string;
  expected_override_id: string;
  reason: string;
}

export interface ParserSnapshot {
  parser_id: string;
  parser_version: string;
  parser_run_id: string;
  parsed_at: string;
  status: string;
  raw_result: ParsedChannelData;
  effective_result: ParsedChannelData;
  discovered_models: string[];
}

export type ParserSnapshotDocument = Record<string, ParserSnapshot>;

export interface RoutingModelSummary {
  model: string;
  strategy: RoutingStrategy;
  accounts: number;
  available_accounts: number;
  inflight: number;
  max_concurrency: number;
  version: number;
}

export type JsonDecimal = number | string;

export interface RoutingCostEvidence {
  kind: "normalized_per_million_tokens" | "effective_prices" | "subscription_included";
  currency: string | null;
  unit: string | null;
  input_price: JsonDecimal | null;
  output_price: JsonDecimal | null;
  cache_read_price: JsonDecimal | null;
  cache_write_price: JsonDecimal | null;
  effective_cost: JsonDecimal;
  partial: boolean;
  provider_group_id: string | null;
  billing_mode: "subscription" | "metered" | "provider_decided";
}

export interface RoutingTableEntry {
  account_id: string;
  display_name: string;
  provider: string;
  base_url_display: string;
  deployment_id: string;
  billing_route_id: string | null;
  billing_mode: "subscription" | "metered" | "provider_decided";
  public_model: string;
  enabled: boolean;
  health: HealthRuntimeSnapshot["health"];
  inflight: number;
  max_concurrency: number;
  cooldown_until: number | null;
  reason_code: string | null;
  exclusion_scope: HealthExclusion["scope"] | null;
  exclusion_source: HealthExclusion["source"] | "administrative" | "quota" | "runtime" | null;
  exclusion_state: "active" | "half_open" | "cleared" | null;
  retry_at: number | null;
  quota: QuotaConfig;
  priority: number;
  weight: number;
  available: boolean;
  unavailable_reason: string | null;
  binding_id: string | null;
  position: number | null;
  strategy: RoutingStrategy | null;
  dynamic_order: boolean;
  sort_reason_codes: string[];
  remaining_quota_ratio: number | null;
  latency_ewma_ms: number | null;
  effective_cost: JsonDecimal | null;
  cost_evidence: RoutingCostEvidence | null;
  manual_order: number | null;
  effective_weight: number;
  routing_paused: boolean;
}

export interface RoutingCandidateOverride {
  binding_id: string;
  manual_order: number | null;
  weight: number | null;
  paused: boolean;
}

export interface RoutingPolicyState {
  status: "loaded";
  model: string;
  strategy: RoutingStrategy;
  version: number;
  overrides: RoutingCandidateOverride[];
}

export interface RoutingPolicyMutation {
  expected_version: number;
  strategy: RoutingStrategy;
}

export interface RoutingCandidateMutation {
  expected_version: number;
  manual_order: number | null;
  weight: number | null;
  paused: boolean;
}

export type ParserOverviewState = "loaded" | "not_run" | "unavailable" | "invalid";

export interface SubscriptionOverview {
  plan_name: string | null;
  status: "active" | "trial" | "expired" | "suspended" | "unknown";
  expires_at: string | null;
  balance: JsonDecimal | null;
  currency: string | null;
  model_count: number;
  limit_count: number;
}

export interface MeteredOverview {
  group_count: number;
  model_count: number;
}

export interface ParserOverview {
  state: ParserOverviewState;
  parser_id: string | null;
  parser_version: string | null;
  status: string | null;
  parsed_at: string | null;
  subscription: SubscriptionOverview | null;
  metered: MeteredOverview | null;
  unresolved_count: number;
  warning_count: number;
  active_override_count: number;
  failure_code: string | null;
}

export interface RuntimeOverview {
  health: HealthRuntimeSnapshot["health"];
  reason_code: string | null;
  inflight: number;
  max_concurrency: number;
  cooldown_until: number | null;
  quota: QuotaConfig;
}

export interface ChannelActivityOverview {
  persistence_available: boolean;
  last_request_at: string | null;
  last_success_at: string | null;
  last_failure_at: string | null;
  last_probe_at: string | null;
}

export interface ChannelOverview {
  channel_id: string;
  account_id: string | null;
  display_name: string;
  provider: string;
  group: string | null;
  base_url_display: string;
  key_mask: string | null;
  administrative_state: AdministrativeState;
  priority: ChannelPriority;
  configured_models: string[];
  schedulable_models: string[];
  unavailable_reason_codes: string[];
  binding_count: number;
  enabled_binding_count: number;
  runtime: RuntimeOverview | null;
  parser: ParserOverview;
  activity: ChannelActivityOverview;
}

export interface AccountPoolOverview {
  status: "loaded";
  channels: ChannelOverview[];
  channel_count: number;
  administratively_enabled_count: number;
  healthy_count: number;
  schedulable_count: number;
  configured_model_count: number;
  schedulable_model_count: number;
  inflight: number;
  max_concurrency: number;
}

export type EventQueryOutcome = "accepted" | "succeeded" | "failed" | "interrupted";

export interface EventLogFilters {
  occurred_after?: string;
  occurred_before?: string;
  channel_id?: string;
  model_id?: string;
  event_type?: string;
  health_outcome?: "succeeded" | "failed";
  health_transition?: HealthTransition;
  reason_code?: string;
  request_id?: string;
  outcome?: EventQueryOutcome;
  cursor?: string;
  limit?: number;
}

export interface EventAuditSummary {
  operation_id: string | null;
  actor_role: "proxy_admin" | "system";
  actor_action: string;
  actor_envelope_id: string;
  outcome: "accepted" | "succeeded" | "failed";
}

export interface EventHealthSummary {
  account_id: string;
  source: "passive_request" | "active_probe";
  outcome: "succeeded" | "failed";
  transition: HealthTransition;
  scope: HealthExclusion["scope"];
  retry_at: string | null;
  probe_trigger: "manual" | "initial" | "half_open" | "idle" | null;
}

export interface EventOperationalSummary {
  source:
    | "parser_task"
    | "parser_snapshot_export"
    | "sync_reconcile"
    | "request_lifecycle"
    | "eligibility_transition"
    | "public_metadata_task";
  operation_id: string;
  outcome: "succeeded" | "failed" | "interrupted";
}

export interface EventLogEntry {
  event_id: string;
  event_type: string;
  occurred_at: string;
  channel_id: string | null;
  model_id: string | null;
  deployment_id: string | null;
  request_id: string | null;
  lease_id: string | null;
  reason_code: string | null;
  actor_type: "user" | "system";
  actor_id: string;
  outcome: EventQueryOutcome;
  safe_details: JsonValue;
  audit: EventAuditSummary | null;
  health: EventHealthSummary | null;
  operational: EventOperationalSummary | null;
}

export interface EventLogPage {
  status: "loaded";
  events: EventLogEntry[];
  next_cursor: string | null;
}

export interface DetailSectionFailure {
  code: string;
  retryable: boolean;
}

export interface DetailSection<T> {
  status: "loaded" | "unavailable";
  data: T | null;
  failure: DetailSectionFailure | null;
}

export interface ChannelAggregateDetail {
  status: "loaded";
  channel: ChannelDetail;
  overview: DetailSection<ChannelOverview>;
  parser: DetailSection<EffectiveParserData>;
  health: DetailSection<ChannelHealthDetail>;
  routes: DetailSection<RoutingTableEntry[]>;
  events: DetailSection<EventLogEntry[]>;
}
