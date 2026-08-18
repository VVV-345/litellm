// 本文件定义 Admin UI 与 account-pool 管理代理之间的稳定数据契约。
export type CapabilityState = "supported" | "unsupported" | "unavailable";

export interface ProviderCapability {
  capability: string;
  state: CapabilityState;
  message: string;
}

export interface ProviderServiceManifest {
  provider_id: string;
  display_name: string;
  default_api_base: string;
  litellm_provider_prefix: string;
  capabilities: ProviderCapability[];
}

export interface ModelOffer {
  model: string;
  input_price_per_million: number | null;
  output_price_per_million: number | null;
  currency: string | null;
  pricing_source: string | null;
}

export interface ProviderValidationResult {
  ok: boolean;
  provider_id: string;
  normalized_api_base: string;
  group: string | null;
  key_fingerprint: string | null;
  message: string;
  capabilities: ProviderCapability[];
  models: ModelOffer[];
}

export interface PoolAccount {
  id: string;
  display_name: string;
  provider: string;
  group: string | null;
  base_url_display: string;
  models: string[];
  runtime: {
    health: string;
    inflight: number;
    max_concurrency: number;
  };
}

export interface CreatePoolAccountRequest {
  id: string;
  display_name: string;
  provider: string;
  group: string | null;
  base_url_display: string;
  enabled: boolean;
  max_concurrency: number;
  priority: number;
  weight: number;
  quotas: { unit: "tokens"; total: null; five_hour: null; weekly: null };
  api_key: string;
  deployments: Array<{ public_model: string; provider_model: string }>;
}

export interface ManagementResult {
  ok: boolean;
  message: string;
}
