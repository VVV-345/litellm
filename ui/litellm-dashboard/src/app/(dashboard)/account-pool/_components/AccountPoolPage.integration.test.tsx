// 本文件验证 Account Pool 渠道查询与解析差异组件在真实页面树中的连接。

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getChannels, getEffectiveData, getEvents, getOverview, getParserHistory, getProviderServices } from "../api";
import AccountPoolPage from "./AccountPoolPage";

vi.mock("@/app/(dashboard)/hooks/models/useModelCostMap", () => ({
  useModelCostMap: vi.fn(() => ({ data: {}, isLoading: false })),
}));

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    getChannels: vi.fn(),
    getEffectiveData: vi.fn(),
    getEvents: vi.fn(),
    getOverview: vi.fn(),
    getParserHistory: vi.fn(),
    getProviderServices: vi.fn(),
  };
});

const mockedGetChannels = vi.mocked(getChannels);
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

describe("AccountPoolPage", () => {
  beforeEach(() => {
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
    expect(mockedGetEvents).toHaveBeenCalledWith("proxy-token", { limit: 50 });
  });
});
