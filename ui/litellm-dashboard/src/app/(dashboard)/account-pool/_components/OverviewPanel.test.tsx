// 本文件验证 Dashboard 聚合总览区分已配置、健康和可调度状态。

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getOverview } from "../api";
import type { AccountPoolOverview } from "../types";
import OverviewPanel from "./OverviewPanel";

vi.mock("../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api")>();
  return { ...original, getOverview: vi.fn() };
});

const overview: AccountPoolOverview = {
  status: "loaded",
  channel_count: 1,
  administratively_enabled_count: 1,
  healthy_count: 1,
  schedulable_count: 0,
  configured_model_count: 2,
  schedulable_model_count: 0,
  inflight: 2,
  max_concurrency: 4,
  channels: [
    {
      channel_id: "channel-1",
      account_id: "primary",
      display_name: "主渠道",
      provider: "openai_compatible",
      group: "default",
      base_url_display: "https://example.com/v1",
      key_mask: "sk-***test",
      administrative_state: "enabled",
      priority: 300,
      configured_models: ["gpt-5.6", "gpt-5.6-mini"],
      schedulable_models: [],
      unavailable_reason_codes: ["weekly_quota"],
      binding_count: 2,
      enabled_binding_count: 2,
      runtime: {
        health: "healthy",
        reason_code: "weekly_quota",
        inflight: 2,
        max_concurrency: 4,
        cooldown_until: null,
        quota: { unit: "usd", total: 75, five_hour: null, weekly: 0 },
      },
      parser: {
        state: "loaded",
        parser_id: "openai_compatible",
        parser_version: "1.0.0",
        status: "partial",
        parsed_at: "2026-08-21T09:00:00Z",
        subscription: {
          plan_name: "专业版",
          status: "active",
          expires_at: null,
          balance: "75.50",
          currency: "USD",
          model_count: 2,
          limit_count: 1,
        },
        metered: { group_count: 1, model_count: 2 },
        unresolved_count: 1,
        warning_count: 1,
        active_override_count: 0,
        failure_code: null,
      },
      activity: {
        persistence_available: true,
        last_request_at: "2026-08-21T09:00:00Z",
        last_success_at: "2026-08-21T09:00:00Z",
        last_failure_at: null,
        last_probe_at: null,
      },
    },
  ],
};

const renderPanel = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <OverviewPanel accessToken="token" />
    </QueryClientProvider>,
  );
};

describe("OverviewPanel", () => {
  beforeEach(() => {
    vi.mocked(getOverview).mockResolvedValue(overview);
  });

  it("shows aggregate totals and keeps healthy distinct from schedulable", async () => {
    renderPanel();

    expect(await screen.findByText("主渠道")).toBeInTheDocument();
    expect(screen.getByText("0/1 可调度")).toBeInTheDocument();
    expect(screen.getByText("1/1 正常")).toBeInTheDocument();
    expect(screen.getByText("周额度已耗尽 (weekly_quota)")).toBeInTheDocument();
    expect(screen.getByText("专业版 / 1 个按量分组")).toBeInTheDocument();
    expect(screen.getByText("openai_compatible 1.0.0 · partial")).toBeInTheDocument();
    expect(screen.getByText("余额/额度 75 usd")).toBeInTheDocument();
    expect(screen.getByText("套餐余额 75.5 USD")).toBeInTheDocument();
  });

  it("shows an explicit error when aggregate data cannot be loaded", async () => {
    vi.mocked(getOverview).mockRejectedValue(new Error("unavailable"));

    renderPanel();

    expect(await screen.findByText("无法读取聚合总览，请检查 Account Pool 数据库和运行服务")).toBeInTheDocument();
  });

  it("opens the event log for a selected channel", async () => {
    const user = userEvent.setup();
    const onOpenChannelEvents = vi.fn();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <OverviewPanel accessToken="token" onOpenChannelEvents={onOpenChannelEvents} />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "查看渠道日志" }));

    expect(onOpenChannelEvents).toHaveBeenCalledWith("channel-1");
    expect(screen.getByRole("row", { name: /主渠道/ }).querySelectorAll("td")).toHaveLength(7);
  });
});
