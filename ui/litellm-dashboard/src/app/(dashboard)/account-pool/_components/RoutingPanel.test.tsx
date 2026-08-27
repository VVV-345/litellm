// 本文件验证 Dashboard 模型调度面板展示正式策略并提交版本化候选设置。

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  getRoutingModels,
  getRoutingPolicy,
  getRoutingTable,
  resetRoutingCandidate,
  updateRoutingCandidate,
  updateRoutingPolicy,
} from "../api";
import type { RoutingModelSummary, RoutingPolicyState, RoutingTableEntry } from "../types";
import RoutingPanel from "./RoutingPanel";

vi.mock("../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api")>();
  return {
    ...original,
    getRoutingModels: vi.fn(),
    getRoutingPolicy: vi.fn(),
    getRoutingTable: vi.fn(),
    updateRoutingPolicy: vi.fn(),
    updateRoutingCandidate: vi.fn(),
    resetRoutingCandidate: vi.fn(),
  };
});

const models: RoutingModelSummary[] = [
  {
    model: "openai/gpt-5.6",
    strategy: "lowest_effective_cost",
    accounts: 1,
    available_accounts: 1,
    inflight: 1,
    max_concurrency: 4,
    version: 3,
  },
];

const policy: RoutingPolicyState = {
  status: "loaded",
  model: "openai/gpt-5.6",
  strategy: "lowest_effective_cost",
  version: 3,
  overrides: [{ binding_id: "binding-1", manual_order: 2, weight: 6, paused: false }],
};

const route: RoutingTableEntry = {
  account_id: "channel-1",
  display_name: "主渠道",
  provider: "openai",
  base_url_display: "https://example.com",
  deployment_id: "deployment-1",
  billing_route_id: null,
  billing_mode: "metered",
  public_model: "openai/gpt-5.6",
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
  quota: { unit: "usd", total: 90, five_hour: null, weekly: null },
  priority: 300,
  weight: 4,
  available: true,
  unavailable_reason: null,
  binding_id: "binding-1",
  position: 1,
  strategy: "lowest_effective_cost",
  dynamic_order: false,
  sort_reason_codes: ["effective_cost", "priority"],
  remaining_quota_ratio: 0.75,
  latency_ewma_ms: 120,
  effective_cost: 3,
  cost_evidence: {
    kind: "normalized_per_million_tokens",
    currency: "USD",
    unit: "million_tokens",
    input_price: 1,
    output_price: 2,
    cache_read_price: null,
    cache_write_price: null,
    effective_cost: "3.00",
    partial: false,
    provider_group_id: "default",
    billing_mode: "metered",
  },
  manual_order: 2,
  effective_weight: 6,
  routing_paused: false,
};

const renderPanel = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <RoutingPanel accessToken="token" />
    </QueryClientProvider>,
  );
};

describe("RoutingPanel", () => {
  beforeEach(() => {
    vi.mocked(getRoutingModels).mockResolvedValue(models);
    vi.mocked(getRoutingPolicy).mockResolvedValue(policy);
    vi.mocked(getRoutingTable).mockResolvedValue([route]);
    vi.mocked(updateRoutingPolicy).mockResolvedValue({ ...policy, version: 4, strategy: "lowest_latency" });
    vi.mocked(updateRoutingCandidate).mockResolvedValue({ ...policy, version: 4 });
    vi.mocked(resetRoutingCandidate).mockResolvedValue({ ...policy, version: 4, overrides: [] });
  });

  it("renders strategy, cost, quota, latency and routing evidence", async () => {
    renderPanel();

    expect(await screen.findByText("主渠道")).toBeInTheDocument();
    expect(screen.getByText(/成本最低优先 · 1\/1 可用/)).toBeInTheDocument();
    expect(screen.getByRole("combobox")).toHaveTextContent("成本最低优先");
    expect(screen.getByText("有效成本 / 渠道优先级")).toBeInTheDocument();
    expect(screen.getByText("余额可用")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("120 ms")).toBeInTheDocument();
    expect(screen.getByText("3 USD/million_tokens")).toBeInTheDocument();
  });

  it("submits versioned candidate overrides and can restore automatic settings", async () => {
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText("主渠道");
    await user.click(screen.getByRole("button", { name: "调整候选" }));

    fireEvent.change(screen.getByLabelText("人工顺序"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("模型权重"), { target: { value: "9" } });
    await user.click(screen.getByRole("button", { name: "保存" }));

    const expectedMutation = {
      expected_version: 3,
      manual_order: 1,
      weight: 9,
      paused: false,
    };
    expect(updateRoutingCandidate).toHaveBeenCalledWith("token", "openai/gpt-5.6", "binding-1", expectedMutation);

    await user.click(screen.getByRole("button", { name: "调整候选" }));
    await user.click(screen.getByRole("button", { name: "恢复自动" }));
    expect(resetRoutingCandidate).toHaveBeenCalledWith("token", "openai/gpt-5.6", "binding-1", 3);
  });

  it("updates the strategy with the current policy version", async () => {
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText("主渠道");

    await user.click(screen.getByRole("combobox"));
    await user.click(screen.getByRole("option", { name: "延迟优先" }));

    expect(updateRoutingPolicy).toHaveBeenCalledWith("token", "openai/gpt-5.6", {
      expected_version: 3,
      strategy: "lowest_latency",
    });
  });

  it("shows a route error instead of reporting an empty candidate list", async () => {
    vi.mocked(getRoutingTable).mockRejectedValue(new Error("route unavailable"));

    renderPanel();

    expect(await screen.findByText("无法读取此模型的运行路由，请检查 Account Pool 调度服务")).toBeInTheDocument();
    expect(screen.queryByText("此模型暂无候选渠道")).not.toBeInTheDocument();
  });
});
