import { describe, expect, it } from "vitest";

import type { ErrorStats } from "../api/AccountPoolManagementApi";
import {
  accountPoolUsableModels,
  accountPoolHiddenModelCount,
  accountPoolVisibleModels,
  filterAccountPoolDashboardEnvironments,
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
    quota: { observed_at: null, plan_type: null, windows: [], balances: [] },
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
  it("combines search, enabled state and runtime status without conflating disabled with an error", () => {
    const ready = environment("ready", "openai_codex");
    const error = { ...ready, id: "account-error", status: "error" as const };
    const disabled = { ...error, id: "account-disabled", enabled: false };
    expect(filterAccountPoolDashboardEnvironments([ready, error, disabled], { search: "account", enabled: "disabled", status: "error" })).toEqual([disabled]);
    expect(filterAccountPoolDashboardEnvironments([ready, error, disabled], { search: "", enabled: "enabled", status: "error" })).toEqual([error]);
    expect(filterAccountPoolDashboardEnvironments([ready, error, disabled], { search: "", enabled: "all", status: "all" })).toEqual([ready, error, disabled]);
  });

  it("searches names and IDs case insensitively and trims whitespace", () => {
    const named = { ...environment("card-id", "kimi"), name: "My Account" };
    expect(filterAccountPoolDashboardEnvironments([named], { search: " MY ACCOUNT ", enabled: "all", status: "all" })).toEqual([named]);
    expect(filterAccountPoolDashboardEnvironments([named], { search: " CARD-ID ", enabled: "all", status: "all" })).toEqual([named]);
  });

  it("filters without mutating or reordering the source cards", () => {
    const first = { ...environment("z", "kimi"), updated_at: "2026-01-01T00:00:00Z" };
    const second = { ...environment("a", "kimi"), updated_at: "2026-10-01T00:00:00Z" };
    const source = Object.freeze([first, second]);
    expect(filterAccountPoolDashboardEnvironments(source, { search: "", enabled: "all", status: "ready" })).toEqual([first, second]);
    expect(source).toEqual([first, second]);
  });

  it("keeps historical requests from deleted accounts in the standard summary", () => {
    const summary = summarizeAccountPoolDashboard([environment("one", "kimi")], new Map(), stats(5, 3, 2));
    expect(summary.totalCards).toBe(1);
    expect(summary.totalRequests).toBe(5);
    expect(summary.successRate).toBe(60);
  });

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
  it("excludes pending authorization and configuration from available cards", () => {
    const ready = environment("ready", "openai_codex");
    const waiting = { ...ready, id: "waiting", status: "awaiting_authorization" as const };
    const pending = { ...ready, id: "pending", configuration_pending: true };
    expect(summarizeAccountPoolDashboard([ready, waiting, pending], new Map()).enabledCards).toBe(1);
  });
});


it("hides unproven, expired and independently cooling access-only models", () => {
  const card = { ...environment("fallback", "openai_codex"), enabled_models: ["model-a", "model-b", "model-c"],
    access_token_fallback: { enabled: true, active: true, failure_source: "refresh", reason: "temporary_access", expires_at: "2090-01-01T00:00:00Z", verified_models: ["model-a", "model-c"] },
    model_cooldowns: [{model: "model-c", retry_at: "2090-01-01T00:00:00Z", reason: "rate_limit", consecutive_timeouts: 0}],
  };
  expect(accountPoolUsableModels(card)).toEqual(["model-a"]);
  expect(accountPoolVisibleModels(card)).toEqual(["model-a"]);
  expect(accountPoolHiddenModelCount(card)).toBe(0);
  expect(accountPoolUsableModels({...card, access_token_fallback: {...card.access_token_fallback, expires_at: "2000-01-01T00:00:00Z"}})).toEqual([]);
  expect(accountPoolUsableModels({...card, access_token_fallback: {...card.access_token_fallback, enabled: false}})).toEqual([]);
  expect(accountPoolUsableModels({...card, access_token_fallback: {...card.access_token_fallback, failure_source: null}})).toEqual(card.enabled_models);
});

it("hides access-only models with exhausted quota until the quota window resets", () => {
  const card: AccountPoolEnvironment = {
    ...environment("fallback", "openai_codex"),
    enabled_models: ["model-a", "model-b"],
    access_token_fallback: {
      enabled: true, active: true, failure_source: "refresh", reason: "temporary_access",
      expires_at: "2090-01-01T00:00:00Z", verified_models: ["model-a", "model-b"],
    },
  };
  const depleted = {
    ...card.quota,
    windows: [{ name: "Weekly", remaining_percent: 0, used_percent: 100, window_minutes: 10080, resets_at: "2090-01-01T00:00:00Z" }],
  };
  expect(accountPoolUsableModels({ ...card, model_quotas: [{ model: "model-a", quota: depleted }] })).toEqual(["model-b"]);
  expect(accountPoolUsableModels({ ...card, quota: depleted })).toEqual([]);
  expect(accountPoolUsableModels({ ...card, quota: depleted, model_quotas: [{ model: "model-a", quota: {
    ...depleted, windows: [{ ...depleted.windows[0], remaining_percent: 50, used_percent: 50 }],
  } }] })).toEqual(["model-a"]);
  expect(accountPoolUsableModels({ ...card, quota: depleted, model_quotas: [{ model: "model-a", quota: {
    ...depleted, windows: [],
  } }] })).toEqual([]);
  expect(accountPoolUsableModels({ ...card, model_quotas: [{ model: "model-a", quota: {
    ...depleted, windows: [{ ...depleted.windows[0], resets_at: null }],
  } }] })).toEqual(["model-b"]);
  expect(accountPoolUsableModels({ ...card, model_quotas: [{ model: "model-a", quota: {
    ...depleted, windows: [{ ...depleted.windows[0], resets_at: "2000-01-01T00:00:00Z" }],
  } }] })).toEqual(["model-a", "model-b"]);
});

it("does not count an access-only card as available when none of its models has valid proof", () => {
  const card: AccountPoolEnvironment = {
    ...environment("fallback", "openai_codex"),
    access_token_fallback: {
      enabled: true, active: false, failure_source: "refresh", reason: "verification_required",
      expires_at: "2090-01-01T00:00:00Z", verified_models: [],
    },
  };
  expect(summarizeAccountPoolDashboard([card], new Map()).enabledCards).toBe(0);
});
