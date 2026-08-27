// 本文件验证 Account Pool 渠道查询与解析差异组件在真实页面树中的连接。

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  getChannelAggregate,
  getChannelHealth,
  getChannels,
  getEffectiveData,
  getEvents,
  getOverview,
  getParserHistory,
  getProviderServices,
} from "../api";
import type { ChannelAggregateDetail } from "../types";
import AccountPoolPage from "./AccountPoolPage";

vi.mock("@/app/(dashboard)/hooks/models/useModelCostMap", () => ({
  useModelCostMap: vi.fn(() => ({ data: {}, isLoading: false })),
}));

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    getChannelAggregate: vi.fn(),
    getChannelHealth: vi.fn(),
    getChannels: vi.fn(),
    getEffectiveData: vi.fn(),
    getEvents: vi.fn(),
    getOverview: vi.fn(),
    getParserHistory: vi.fn(),
    getProviderServices: vi.fn(),
  };
});

const mockedGetChannels = vi.mocked(getChannels);
const mockedGetChannelAggregate = vi.mocked(getChannelAggregate);
const mockedGetChannelHealth = vi.mocked(getChannelHealth);
const mockedGetEffectiveData = vi.mocked(getEffectiveData);
const mockedGetEvents = vi.mocked(getEvents);
const mockedGetOverview = vi.mocked(getOverview);
const mockedGetParserHistory = vi.mocked(getParserHistory);
const mockedGetProviderServices = vi.mocked(getProviderServices);
const emptyOverview = {
  status: "loaded" as const,
  channels: [],
  channel_count: 0,
  administratively_enabled_count: 0,
  healthy_count: 0,
  schedulable_count: 0,
  configured_model_count: 0,
  schedulable_model_count: 0,
  inflight: 0,
  max_concurrency: 0,
};
const channelFixture = {
  channel_id: "10000000-0000-0000-0000-000000000001",
  display_name: "OpenAI 主渠道",
  provider: "openai_compatible",
  group: null,
  base_url_display: "https://gateway.example.com/v1",
  administrative_state: "enabled" as const,
  max_concurrency: 8,
  priority: 300 as const,
  weight: 20,
  key_mask: "sk-***main",
  binding_count: 2,
  enabled_binding_count: 1,
  models: ["gpt-5.6"],
  created_at: "2026-08-19T00:00:00Z",
  updated_at: "2026-08-19T00:00:00Z",
};
const effectiveDataFixture = {
  status: "loaded" as const,
  channel_id: "10000000-0000-0000-0000-000000000001",
  parser_run_id: "20000000-0000-0000-0000-000000000002",
  parser_id: "fixture-parser",
  parser_version: "1.0.0",
  parsed_at: "2026-08-19T00:00:00Z",
  parser_status: "partial",
  raw_result: {
    subscription: { plan_name: "Starter" },
    metered: null,
    billing_routes: [],
    capabilities: [],
    unresolved_fields: [],
    evidence: [],
    warnings: [],
  },
  effective_result: {
    subscription: { plan_name: "Pro" },
    metered: null,
    billing_routes: [],
    capabilities: [],
    unresolved_fields: [],
    evidence: [],
    warnings: [],
  },
  active_overrides: [
    {
      override_id: "30000000-0000-0000-0000-000000000003",
      field_path: "/subscription/plan_name",
      source_parser_run_id: "20000000-0000-0000-0000-000000000002",
      occurred_at: "2026-08-19T00:00:00Z",
    },
  ],
  applied_override_ids: ["30000000-0000-0000-0000-000000000003"],
  override_failures: [],
};
const aggregateFixture: ChannelAggregateDetail = {
  status: "loaded",
  channel: {
    channel_id: channelFixture.channel_id,
    display_name: channelFixture.display_name,
    provider: channelFixture.provider,
    group: channelFixture.group,
    base_url_display: channelFixture.base_url_display,
    administrative_state: channelFixture.administrative_state,
    max_concurrency: channelFixture.max_concurrency,
    priority: channelFixture.priority,
    weight: channelFixture.weight,
    quotas: { unit: "tokens", total: null, five_hour: null, weekly: null },
    key_mask: channelFixture.key_mask,
    bindings: [],
  },
  overview: { status: "unavailable", data: null, failure: { code: "runtime_not_projected", retryable: true } },
  parser: { status: "unavailable", data: null, failure: { code: "run_not_found", retryable: false } },
  health: { status: "unavailable", data: null, failure: { code: "runtime_unavailable", retryable: true } },
  routes: { status: "unavailable", data: null, failure: { code: "runtime_unavailable", retryable: true } },
  events: { status: "loaded", data: [], failure: null },
};
const healthFixture = {
  channel_id: channelFixture.channel_id,
  account_id: "account-1",
  runtime: {
    account_id: "account-1",
    enabled: true,
    health: "healthy" as const,
    inflight: 0,
    max_concurrency: 8,
    cooldown_until: null,
    consecutive_failures: 0,
    reason_code: null,
    quota: { unit: "tokens" as const, total: null, five_hour: null, weekly: null },
  },
  exclusions: [],
  activities: [],
  events: [],
  persistence_available: true,
};

describe("AccountPoolPage", () => {
  beforeEach(() => {
    mockedGetChannelAggregate.mockResolvedValue(aggregateFixture);
    mockedGetChannelHealth.mockResolvedValue(healthFixture);
    mockedGetOverview.mockResolvedValue(emptyOverview);
    mockedGetChannels.mockResolvedValue({
      channels: [channelFixture],
    });
    mockedGetProviderServices.mockResolvedValue([]);
    mockedGetEffectiveData.mockResolvedValue(effectiveDataFixture);
    mockedGetEvents.mockResolvedValue({ status: "loaded", events: [], next_cursor: null });
    mockedGetParserHistory.mockResolvedValue({
      status: "loaded",
      channel_id: "10000000-0000-0000-0000-000000000001",
      runs: [],
    });
  });

  it("renders the catalog identity and raw/effective parser values", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={queryClient}>
        <AccountPoolPage accessToken="proxy-token" userRole="proxy_admin" />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("tab", { name: "渠道管理" }));
    expect(await screen.findAllByText("OpenAI 主渠道")).not.toHaveLength(0);
    expect(screen.getByText("高")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "解析数据" }));
    expect(await screen.findByText('"Starter"')).toBeInTheDocument();
    expect(screen.getByText('"Pro"')).toBeInTheDocument();
    expect(screen.getByText("人工修正")).toBeInTheDocument();
    expect(mockedGetChannels).toHaveBeenCalledWith("proxy-token");
  });

  it("opens the event tab without retaining a hidden channel filter", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={queryClient}>
        <AccountPoolPage accessToken="proxy-token" userRole="proxy_admin" />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("tab", { name: "事件日志" }));

    expect(await screen.findByText("当前筛选条件下没有事件")).toBeInTheDocument();
    expect(mockedGetEvents).toHaveBeenCalledWith("proxy-token", { limit: 10 });
  });

  it("exposes parser and health as top-level workspaces", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={queryClient}>
        <AccountPoolPage accessToken="proxy-token" userRole="proxy_admin" />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("tab", { name: "解析器" }));
    expect(await screen.findByText("查看渠道的模型发现、套餐、按量价格和人工修正")).toBeInTheDocument();
    expect(await screen.findByText('"Starter"')).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "健康与冷却" }));
    expect(await screen.findByText("查看健康探测、并发占用、额度窗口和当前排除原因")).toBeInTheDocument();
    expect(await screen.findByText("最近健康事件")).toBeInTheDocument();
  });
});
