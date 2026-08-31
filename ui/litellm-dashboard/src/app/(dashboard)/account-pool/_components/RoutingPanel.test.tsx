// 本文件验证 Dashboard 模型调度面板展示正式策略并提交版本化候选设置。

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  getRoutingModels,
  getRoutingPolicy,
  getRoutingTable,
  updateRoutingCandidate,
  updateRoutingOrder,
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
    updateRoutingOrder: vi.fn(),
  };
});

const models: RoutingModelSummary[] = [
  {
    model: "openai/gpt-5.6",
    strategy: "lowest_effective_cost",
    accounts: 2,
    available_accounts: 2,
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
  sort_reason_codes: ["effective_cost", "channel_priority"],
  remaining_quota_ratio: 0.75,
  remaining_quota: null,
  remaining_quota_unit: null,
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

const secondaryRoute: RoutingTableEntry = {
  ...route,
  account_id: "channel-2",
  display_name: "备用渠道",
  deployment_id: "deployment-2",
  binding_id: "binding-2",
  position: 2,
  manual_order: null,
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
    vi.mocked(getRoutingTable).mockResolvedValue([route, secondaryRoute]);
    vi.mocked(updateRoutingPolicy).mockResolvedValue({ ...policy, version: 4, strategy: "lowest_latency" });
    vi.mocked(updateRoutingCandidate).mockResolvedValue({ ...policy, version: 4 });
    vi.mocked(updateRoutingOrder).mockResolvedValue({ ...policy, version: 4 });
  });

  it("renders strategy, cost, quota, latency and routing evidence", async () => {
    renderPanel();

    expect(await screen.findByText("主渠道")).toBeInTheDocument();
    expect(screen.getByText(/成本最低优先 · 2\/2 可用/)).toBeInTheDocument();
    expect(screen.getByRole("combobox")).toHaveTextContent("成本最低优先");
    expect(screen.getAllByText("有效成本 / 渠道优先级")).toHaveLength(2);
    expect(screen.getAllByText("余额可用")).toHaveLength(2);
    expect(screen.getAllByText("75%")).toHaveLength(2);
    expect(screen.getAllByText("120 ms")).toHaveLength(2);
    expect(screen.getAllByText("3 USD/million_tokens")).toHaveLength(2);
  });

  it("submits versioned candidate settings without a manual order field", async () => {
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText("主渠道");
    await user.click(screen.getAllByRole("button", { name: "调整候选" })[0]!);

    expect(screen.queryByLabelText("人工顺序")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("模型权重"), { target: { value: "9" } });
    await user.click(screen.getByRole("button", { name: "保存" }));

    const expectedMutation = {
      expected_version: 3,
      weight: 9,
      paused: false,
    };
    expect(updateRoutingCandidate).toHaveBeenCalledWith("token", "openai/gpt-5.6", "binding-1", expectedMutation);
  });

  it("keeps a dragged order local until the user saves it", async () => {
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText("备用渠道");
    const handles = screen.getAllByTitle("拖拽调整顺序，保存后生效");
    const dataTransfer = {
      effectAllowed: "",
      getData: vi.fn(() => "binding-2"),
      setData: vi.fn(),
    } as unknown as DataTransfer;
    const firstRow = screen.getByText("主渠道").closest("tr");

    expect(firstRow).not.toBeNull();
    fireEvent.dragStart(handles[1], { dataTransfer });
    fireEvent.dragOver(firstRow!, { dataTransfer });
    fireEvent.drop(firstRow!, { dataTransfer });

    expect(updateRoutingOrder).not.toHaveBeenCalled();
    await user.click(await screen.findByRole("button", { name: "保存顺序" }));

    await waitFor(() =>
      expect(updateRoutingOrder).toHaveBeenCalledWith("token", "openai/gpt-5.6", {
        expected_version: 3,
        binding_ids: ["binding-2", "binding-1"],
      }),
    );
  });

  it("shows remaining package usage and included cost", async () => {
    vi.mocked(getRoutingTable).mockResolvedValue([
      {
        ...route,
        billing_mode: "subscription",
        remaining_quota: "12",
        remaining_quota_unit: "provider_units",
        effective_cost: 0,
        cost_evidence: {
          kind: "subscription_included",
          currency: null,
          unit: null,
          input_price: null,
          output_price: null,
          cache_read_price: null,
          cache_write_price: null,
          effective_cost: 0,
          partial: false,
          provider_group_id: null,
          billing_mode: "subscription",
        },
      },
    ]);

    renderPanel();

    expect(await screen.findByText("套餐余量 12 用量")).toBeInTheDocument();
    expect(screen.getByText("套餐内包含")).toBeInTheDocument();
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
