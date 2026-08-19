// 本文件验证健康面板能展示运行状态、排除原因和脱敏事件。

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getChannelHealth } from "../api";
import type { ChannelHealthDetail } from "../types";
import HealthStatusPanel from "./HealthStatusPanel";

vi.mock("../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api")>();
  return { ...original, getChannelHealth: vi.fn() };
});

const detail: ChannelHealthDetail = {
  channel_id: "channel-1",
  account_id: "account-1",
  persistence_available: true,
  runtime: {
    account_id: "account-1",
    enabled: true,
    health: "cooldown",
    inflight: 1,
    max_concurrency: 3,
    cooldown_until: 1_800_000_000,
    consecutive_failures: 1,
    reason_code: "rate_limited",
    quota: { unit: "tokens", total: 1000, five_hour: 500, weekly: null },
  },
  exclusions: [
    {
      scope: "deployment",
      source: "restriction",
      account_id: "account-1",
      model: "model-a",
      deployment_id: "deployment-a",
      billing_route_id: null,
      reason_code: "rate_limited",
      starts_at: 1_700_000_000,
      retry_at: 1_800_000_000,
      state: "active",
    },
  ],
  activities: [
    {
      channel_id: "channel-1",
      account_id: "account-1",
      model_id: "model-a",
      deployment_id: "deployment-a",
      last_request_at: "2026-08-20T01:00:00Z",
      last_success_at: null,
      last_failure_at: "2026-08-20T01:00:00Z",
      last_probe_at: null,
      last_probe_success_at: null,
      last_probe_failure_at: null,
      updated_at: "2026-08-20T01:00:00Z",
    },
  ],
  events: [
    {
      event: {
        event_id: "event-1",
        event_type: "passive_health_result",
        occurred_at: "2026-08-20T01:00:00Z",
        channel_id: "channel-1",
        model_id: "model-a",
        deployment_id: "deployment-a",
        request_id: "request-1",
        lease_id: "lease-1",
        reason_code: "rate_limited",
        actor_type: "system",
        actor_id: "account_pool_gateway",
        safe_details: {
          kind: "passive_health_result",
          outcome: "failed",
          transition: "cooldown",
          response_status_code: 429,
          latency_ms: 120,
        },
      },
      health: {
        event_id: "event-1",
        account_id: "account-1",
        source: "passive_request",
        outcome: "failed",
        transition: "cooldown",
        scope: "deployment",
        retry_at: "2026-08-20T02:00:00Z",
        probe_trigger: null,
      },
    },
  ],
};

describe("HealthStatusPanel", () => {
  beforeEach(() => {
    vi.mocked(getChannelHealth).mockResolvedValue(detail);
  });

  it("renders current cooldown and recent redacted event", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <HealthStatusPanel accessToken="token" channelId="channel-1" />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("冷却中")).toBeInTheDocument();
    expect(screen.getAllByText("请求限流").length).toBeGreaterThan(0);
    expect(screen.getAllByText("deployment-a").length).toBeGreaterThan(0);
    expect(screen.getByText("120 ms")).toBeInTheDocument();
  });
});
