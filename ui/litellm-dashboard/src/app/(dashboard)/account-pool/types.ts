// 本文件定义 Account Pool Dashboard 使用的脱敏渠道、解析任务和解析结果类型。

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export interface ChannelSummary {
  channel_id: string;
  display_name: string;
  provider: string;
  group: string | null;
  base_url_display: string;
  administrative_state: "enabled" | "paused" | "disabled" | "pending_delete";
  max_concurrency: number;
  priority: number;
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
  group: string | null;
  base_url_display: string;
  administrative_state: AdministrativeState;
  max_concurrency: number;
  priority: number;
  weight: number;
  quotas: QuotaConfig;
  api_key: string | null;
  bindings: ChannelBindingInput[];
}

export interface ChannelDetail extends Omit<ChannelMutationRequest, "api_key"> {
  channel_id: string;
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
  api_base: string;
  api_key: string;
  group: string | null;
  explicit_parser_id: string | null;
  openai_compatible: boolean;
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
