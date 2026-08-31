// 本文件验证渠道综合详情完整展示，以及辅助分区失败时仍保留基础配置。

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getChannelAggregate } from "../api";
import type { ChannelAggregateDetail } from "../types";
import ChannelAggregatePanel from "./ChannelAggregatePanel";

vi.mock("../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api")>();
  return { ...original, getChannelAggregate: vi.fn() };
});

const aggregate: ChannelAggregateDetail = {
  status: "loaded",
  channel: {
    channel_id: "channel-1",
    display_name: "主渠道",
    provider: "openai_compatible",
    model_discovery_provider_id: "openai_compatible",
    group: "default",
    base_url_display: "https://example.com/v1",
    administrative_state: "enabled",
    max_concurrency: 4,
    priority: 300,
    weight: 2,
    quotas: { unit: "tokens", total: null, five_hour: null, weekly: null },
    key_mask: "sk-***test",
    bindings: [
      {
        binding_id: "binding-1",
        public_model: "gpt-5.6",
        provider_model: "openai/gpt-5.6",
        litellm_deployment_id: "deployment-1",
        ownership: "pool_managed",
        enabled: true,
      },
    ],
  },
  overview: {
    status: "loaded",
    failure: null,
    data: {
      channel_id: "channel-1",
      account_id: "primary",
      display_name: "主渠道",
      provider: "openai_compatible",
      group: "default",
      base_url_display: "https://example.com/v1",
      key_mask: "sk-***test",
      administrative_state: "enabled",
      priority: 300,
      configured_models: ["gpt-5.6"],
      schedulable_models: ["gpt-5.6"],
      unavailable_reason_codes: [],
      binding_count: 1,
      enabled_binding_count: 1,
      runtime: {
        health: "healthy",
        reason_code: null,
        inflight: 1,
        max_concurrency: 4,
        cooldown_until: null,
        quota: { unit: "tokens", total: null, five_hour: null, weekly: null },
      },
      parser: {
        state: "loaded",
        parser_id: "openai_compatible",
        parser_version: "1.0.0",
        status: "success",
        parsed_at: "2026-08-22T08:00:00Z",
        subscription: null,
        metered: null,
        unresolved_count: 0,
        warning_count: 0,
        active_override_count: 0,
        failure_code: null,
      },
      activity: {
        persistence_available: true,
        last_request_at: "2026-08-22T08:00:00Z",
        last_success_at: "2026-08-22T08:00:00Z",
        last_failure_at: null,
        last_probe_at: null,
      },
    },
  },
  parser: {
    status: "loaded",
    failure: null,
    data: {
      status: "loaded",
      channel_id: "channel-1",
      parser_run_id: "parser-run-1",
      parser_id: "openai_compatible",
      parser_version: "1.0.0",
      parsed_at: "2026-08-22T08:00:00Z",
      parser_status: "success",
      raw_result: {
        subscription: null,
        metered: null,
        billing_routes: [],
        capabilities: [],
        unresolved_fields: [],
        evidence: [],
        warnings: [],
      },
      effective_result: {
        subscription: { plan_name: "专业版", limits: [{ window_type: "rolling", remaining: "500" }] },
        metered: { groups: [{ group_name: "默认分组", effective_input_price: "1.50" }] },
        billing_routes: [],
        capabilities: ["models", "pricing"],
        unresolved_fields: [],
        evidence: [],
        warnings: [],
      },
      active_overrides: [],
      applied_override_ids: [],
      override_failures: [],
    },
  },
  health: {
    status: "loaded",
    failure: null,
    data: {
      channel_id: "channel-1",
      account_id: "primary",
      runtime: {
        account_id: "primary",
        enabled: true,
        health: "healthy",
        inflight: 1,
        max_concurrency: 4,
        cooldown_until: null,
        consecutive_failures: 0,
        reason_code: null,
        quota: { unit: "tokens", total: null, five_hour: null, weekly: null },
      },
      exclusions: [],
      activities: [],
      events: [],
      persistence_available: true,
    },
  },
  routes: {
    status: "loaded",
    failure: null,
    data: [
      {
        account_id: "primary",
        display_name: "主渠道",
        provider: "openai_compatible",
        base_url_display: "https://example.com/v1",
        deployment_id: "deployment-1",
        billing_route_id: null,
        billing_mode: "provider_decided",
        public_model: "gpt-5.6",
        enabled: true,
        health: "healthy",
        inflight: 1,
        max_concurrency: 4,
        cooldown_until: null,
        reason_code: null,
        exclusion_scope: null,
        exclusion_source: null,
        exclusion_state: null,
        retry_at: null,
        quota: { unit: "tokens", total: null, five_hour: null, weekly: null },
        priority: 300,
        weight: 2,
        available: true,
        unavailable_reason: null,
        binding_id: "binding-1",
        position: 1,
        strategy: "lowest_effective_cost",
        dynamic_order: false,
        sort_reason_codes: ["lowest_effective_cost"],
        remaining_quota_ratio: null,
        remaining_quota: null,
        remaining_quota_unit: null,
        latency_ewma_ms: 120,
        effective_cost: "3.00",
        cost_evidence: null,
        manual_order: null,
        effective_weight: 2,
        routing_paused: false,
      },
    ],
  },
  events: {
    status: "loaded",
    failure: null,
    data: [
      {
        event_id: "event-1",
        event_type: "channel_update",
        occurred_at: "2026-08-22T08:05:00Z",
        channel_id: "channel-1",
        model_id: null,
        deployment_id: null,
        request_id: "request-1",
        lease_id: null,
        reason_code: null,
        actor_type: "user",
        actor_id: "admin-1",
        outcome: "succeeded",
        safe_details: {},
        audit: null,
        health: null,
        operational: null,
      },
    ],
  },
};

const onOpenChannelEvents = vi.fn();

const renderPanel = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ChannelAggregatePanel accessToken="token" channelId="channel-1" onOpenChannelEvents={onOpenChannelEvents} />
    </QueryClientProvider>,
  );
};

describe("ChannelAggregatePanel", () => {
  beforeEach(() => {
    onOpenChannelEvents.mockReset();
    vi.mocked(getChannelAggregate).mockResolvedValue(aggregate);
  });

  it("shows configuration, parser data, routing position, and recent events", async () => {
    renderPanel();

    expect(await screen.findByText("https://example.com/v1")).toBeInTheDocument();
    expect(screen.getByText(/专业版/)).toBeInTheDocument();
    expect(screen.getByText("lowest_effective_cost")).toBeInTheDocument();
    expect(screen.getByText("channel_update")).toBeInTheDocument();
    expect(screen.getByText("request-1")).toBeInTheDocument();
  });

  it("opens the channel-scoped paged event log from the recent event preview", async () => {
    const user = userEvent.setup();
    renderPanel();

    await screen.findByText("channel_update");
    await user.click(screen.getByRole("button", { name: "查看完整日志" }));

    expect(onOpenChannelEvents).toHaveBeenCalledWith("channel-1");
  });

  it("keeps base configuration visible when parser, health, routes, and events are unavailable", async () => {
    const unavailable: ChannelAggregateDetail = {
      ...aggregate,
      parser: { status: "unavailable", data: null, failure: { code: "parser_unavailable", retryable: true } },
      health: { status: "unavailable", data: null, failure: { code: "runtime_unavailable", retryable: true } },
      routes: { status: "unavailable", data: null, failure: { code: "routing_unavailable", retryable: true } },
      events: { status: "unavailable", data: null, failure: { code: "database_unavailable", retryable: true } },
    };
    vi.mocked(getChannelAggregate).mockResolvedValue(unavailable);

    renderPanel();

    expect(await screen.findByText("https://example.com/v1")).toBeInTheDocument();
    expect(screen.getByText("parser_unavailable")).toBeInTheDocument();
    expect(screen.getByText("runtime_unavailable")).toBeInTheDocument();
    expect(screen.getByText("routing_unavailable")).toBeInTheDocument();
    expect(screen.getByText("database_unavailable")).toBeInTheDocument();
  });
});
