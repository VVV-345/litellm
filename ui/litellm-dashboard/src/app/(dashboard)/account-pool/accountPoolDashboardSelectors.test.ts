import { describe, expect, it } from "vitest";

import type { ErrorStats } from "./AccountPoolManagementApi";
import {
  accountPoolHiddenModelCount,
  accountPoolVisibleModels,
  groupAccountPoolEnvironments,
  summarizeAccountPoolDashboard,
} from "./accountPoolDashboardSelectors";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";

const environment = (id: string, supplier: AccountPoolEnvironment["supplier"]): AccountPoolEnvironment =>
  ({
    id,
    version: 1,
    name: id,
    provider: "openai",
    channel: "cliproxyapi",
    supplier,
    authorization_flow: "browser_oauth",
    status: "ready",
    desired_configuration_version: 0,
    observed_configuration_version: 0,
    configuration_pending: false,
    enabled: true,
    manual_cooldown: false,
    concurrency_limit: 2,
    proxy_mode: "default_gateway",
    proxy_profile_id: null,
    available_models: ["model-a"],
    enabled_models: ["model-a"],
    quota: { observed_at: null, plan_type: null, windows: [] },
    model_quotas: [],
    cooldown_until: null,
    automatic_cooldown: false,
    last_error: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  }) as AccountPoolEnvironment;

const stats = (total_requests: number, succeeded_requests: number, failed_requests: number): ErrorStats => ({
  total_requests,
  succeeded_requests,
  failed_requests,
  retried_requests: 0,
  input_tokens: 100,
  output_tokens: 50,
  known_cost_requests: 0,
  total_cost_usd: null,
  average_duration_ms: null,
  recent_errors: [],
  card_id: null,
  account_id: null,
  model: null,
});

describe("account pool dashboard selectors", () => {
  it("aggregates request and token totals without treating missing stats as failures", () => {
    const environments = [environment("one", "openai_codex"), environment("two", "kimi")];
    const summary = summarizeAccountPoolDashboard(environments, new Map([["one", stats(4, 3, 1)]]));
    const expectedSummary = {
      totalCards: 2,
      enabledCards: 2,
      totalRequests: 4,
      successfulRequests: 3,
      failedRequests: 1,
      totalTokens: 150,
      successRate: 75,
    };

    expect(summary).toMatchObject(expectedSummary);
  });

  it("groups cards by supplier and limits the card preview to three models", () => {
    const card = environment("one", "openai_codex");
    const withModels = { ...card, enabled_models: ["a", "b", "c", "d"] } as AccountPoolEnvironment;

    expect(groupAccountPoolEnvironments([card, environment("two", "openai_codex")])).toHaveLength(1);
    expect(accountPoolVisibleModels(withModels)).toEqual(["a", "b", "c"]);
    expect(accountPoolHiddenModelCount(withModels)).toBe(1);
  });
});
