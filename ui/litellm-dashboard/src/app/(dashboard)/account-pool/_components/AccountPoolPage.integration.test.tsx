// 本文件验证 Account Pool 渠道查询与解析差异组件在真实页面树中的连接。

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getChannels, getEffectiveData, getParserHistory, getProviderServices } from "../api";
import AccountPoolPage from "./AccountPoolPage";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    getChannels: vi.fn(),
    getEffectiveData: vi.fn(),
    getParserHistory: vi.fn(),
    getProviderServices: vi.fn(),
  };
});

const mockedGetChannels = vi.mocked(getChannels);
const mockedGetEffectiveData = vi.mocked(getEffectiveData);
const mockedGetParserHistory = vi.mocked(getParserHistory);
const mockedGetProviderServices = vi.mocked(getProviderServices);

describe("AccountPoolPage", () => {
  beforeEach(() => {
    mockedGetChannels.mockResolvedValue({
      channels: [
        {
          channel_id: "10000000-0000-0000-0000-000000000001",
          display_name: "OpenAI 主渠道",
          provider: "openai_compatible",
          group: null,
          base_url_display: "https://gateway.example.com/v1",
          administrative_state: "enabled",
          max_concurrency: 8,
          priority: 10,
          weight: 20,
          key_mask: "sk-***main",
          binding_count: 2,
          enabled_binding_count: 1,
          models: ["gpt-5.6"],
          created_at: "2026-08-19T00:00:00Z",
          updated_at: "2026-08-19T00:00:00Z",
        },
      ],
    });
    mockedGetProviderServices.mockResolvedValue([]);
    mockedGetEffectiveData.mockResolvedValue({
      status: "loaded",
      channel_id: "10000000-0000-0000-0000-000000000001",
      parser_run_id: "20000000-0000-0000-0000-000000000002",
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
    });
    mockedGetParserHistory.mockResolvedValue({
      status: "loaded",
      channel_id: "10000000-0000-0000-0000-000000000001",
      runs: [],
    });
  });

  it("renders the catalog identity and raw/effective parser values", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={queryClient}>
        <AccountPoolPage accessToken="proxy-token" userRole="proxy_admin" />
      </QueryClientProvider>,
    );

    expect(await screen.findAllByText("OpenAI 主渠道")).not.toHaveLength(0);
    expect(await screen.findByText('"Starter"')).toBeInTheDocument();
    expect(screen.getByText('"Pro"')).toBeInTheDocument();
    expect(screen.getByText("人工修正")).toBeInTheDocument();
    expect(mockedGetChannels).toHaveBeenCalledWith("proxy-token");
  });
});
