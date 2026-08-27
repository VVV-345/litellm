// 本文件验证自动排序预览只使用可比较的当前运行信号，并保持不可用候选靠后。

import { describe, expect, it } from "vitest";

import { buildAutoRankingPreview } from "./autoRankingPreview";
import type { RoutingTableEntry } from "./types";

const route = (overrides: Partial<RoutingTableEntry>): RoutingTableEntry => ({
  account_id: "channel-a",
  display_name: "渠道 A",
  provider: "openai_compatible",
  base_url_display: "https://gateway.example/v1",
  deployment_id: "deployment-a",
  billing_route_id: null,
  billing_mode: "provider_decided",
  public_model: "model-a",
  enabled: true,
  health: "healthy",
  inflight: 0,
  max_concurrency: 10,
  cooldown_until: null,
  reason_code: null,
  exclusion_scope: null,
  exclusion_source: null,
  exclusion_state: null,
  retry_at: null,
  quota: { unit: "tokens", total: 100, five_hour: null, weekly: null },
  priority: 200,
  weight: 1,
  available: true,
  unavailable_reason: null,
  binding_id: "binding-a",
  position: 1,
  strategy: "priority",
  dynamic_order: false,
  sort_reason_codes: [],
  remaining_quota_ratio: null,
  latency_ewma_ms: null,
  effective_cost: null,
  cost_evidence: null,
  manual_order: null,
  effective_weight: 1,
  routing_paused: false,
  ...overrides,
});

describe("auto ranking preview", () => {
  it("ranks available routes with lower latency, higher quota, and lower comparable cost first", () => {
    const preview = buildAutoRankingPreview([
      route({
        account_id: "slow-expensive",
        deployment_id: "slow-expensive",
        latency_ewma_ms: 300,
        remaining_quota_ratio: 0.2,
        effective_cost: 12,
        cost_evidence: {
          kind: "normalized_per_million_tokens",
          currency: "USD",
          unit: "million_tokens",
          input_price: 4,
          output_price: 8,
          cache_read_price: null,
          cache_write_price: null,
          effective_cost: 12,
          partial: false,
          provider_group_id: null,
          billing_mode: "metered",
        },
      }),
      route({
        account_id: "fast-cheap",
        deployment_id: "fast-cheap",
        latency_ewma_ms: 50,
        remaining_quota_ratio: 0.9,
        effective_cost: 3,
        cost_evidence: {
          kind: "normalized_per_million_tokens",
          currency: "USD",
          unit: "million_tokens",
          input_price: 1,
          output_price: 2,
          cache_read_price: null,
          cache_write_price: null,
          effective_cost: 3,
          partial: false,
          provider_group_id: null,
          billing_mode: "metered",
        },
      }),
    ]);

    expect(preview.map((entry) => entry.route.account_id)).toEqual(["fast-cheap", "slow-expensive"]);
    expect(preview[0]).toMatchObject({ position: 1, score: 100, signals: ["latency", "quota", "cost"] });
  });

  it("does not compare prices that use different units and keeps unavailable routes after candidates", () => {
    const preview = buildAutoRankingPreview([
      route({
        account_id: "available",
        latency_ewma_ms: 100,
        effective_cost: 2,
        cost_evidence: {
          kind: "effective_prices",
          currency: "USD",
          unit: "million_tokens",
          input_price: 1,
          output_price: 1,
          cache_read_price: null,
          cache_write_price: null,
          effective_cost: 2,
          partial: false,
          provider_group_id: null,
          billing_mode: "metered",
        },
      }),
      route({
        account_id: "different-unit",
        deployment_id: "different-unit",
        latency_ewma_ms: 1,
        effective_cost: 1,
        cost_evidence: {
          kind: "effective_prices",
          currency: "USD",
          unit: "request",
          input_price: 1,
          output_price: null,
          cache_read_price: null,
          cache_write_price: null,
          effective_cost: 1,
          partial: true,
          provider_group_id: null,
          billing_mode: "metered",
        },
      }),
      route({
        account_id: "unavailable",
        deployment_id: "unavailable",
        available: false,
        health: "cooldown",
        unavailable_reason: "cooldown",
      }),
    ]);

    expect(preview.map((entry) => entry.route.account_id)).toEqual(["different-unit", "available", "unavailable"]);
    expect(preview[0].signals).toEqual(["latency"]);
    expect(preview[2]).toMatchObject({ position: 3, score: null, signals: [] });
  });
});
