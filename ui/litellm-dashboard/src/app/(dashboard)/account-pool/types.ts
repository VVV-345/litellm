// 本文件定义 Account Pool Dashboard 使用的脱敏渠道、解析任务和解析结果类型。

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export interface ChannelSummary {
  channel_id: string;
  display_name: string;
  provider: string;
  group: string | null;
  base_url_display: string;
  administrative_state: "enabled" | "paused" | "disabled";
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
