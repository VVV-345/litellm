/** 本文件验证额度页展示完整订阅字段、全部额度窗口和刷新诊断。 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AccountPoolQuotaPanel } from "./AccountPoolQuotaPanel";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";

const environment: AccountPoolEnvironment = {
  id: "00000000-0000-4000-8000-000000000001",
  version: 1,
  desired_configuration_version: 0,
  observed_configuration_version: 0,
  name: "xAI account",
  provider: "openai",
  channel: "cliproxyapi",
  supplier: "xai",
  authorization_flow: "device_code",
  status: "ready",
  configuration_pending: false,
  enabled: true,
  manual_cooldown: false,
  concurrency_limit: 2,
  proxy_mode: "default_gateway",
  proxy_profile_id: null,
  available_models: ["grok-code-fast-1"],
  enabled_models: ["grok-code-fast-1"],
  quota: {
    observed_at: "2026-09-14T00:00:00Z",
    refresh_attempted_at: "2026-09-14T00:05:00Z",
    source: "cliproxyapi_cache",
    plan_type: "SuperGrok Heavy",
    subscription_status: "SUBSCRIPTION_STATUS_ACTIVE",
    subscription_active_start: "2026-09-01T00:00:00Z",
    subscription_active_until: "2026-10-01T00:00:00Z",
    prepaid_balance: 700,
    refresh_status: "partial",
    refresh_error: "GET https://grok.com/rest/tasks/usage: HTTP 503",
    balances: [{ name: "GOOGLE_ONE_AI", available: 25000, minimum_required: 50, unit: "credits" }],
    windows: [
      {
        name: "Weekly",
        used_percent: 20,
        remaining_percent: 80,
        window_minutes: 10080,
        starts_at: "2026-09-10T00:00:00Z",
        resets_at: "2026-09-17T00:00:00Z",
        used: 200,
        total: 1000,
        remaining: 800,
        unit: "credits",
      },
      {
        name: "Tasks: Frequent",
        used_percent: 10,
        remaining_percent: 90,
        window_minutes: 10080,
        used: 2,
        total: 20,
        remaining: 18,
        unit: "tasks",
      },
    ],
  },
  model_quotas: [],
  cooldown_until: null,
  automatic_cooldown: false,
  last_error: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-14T00:00:00Z",
};

describe("AccountPoolQuotaPanel", () => {
  it("shows every quota window with exact amounts and subscription metadata", () => {
    render(<AccountPoolQuotaPanel environments={[environment]} onRefresh={vi.fn()} />);

    expect(screen.getByText("SuperGrok Heavy")).toBeInTheDocument();
    expect(screen.getByText("SUBSCRIPTION_STATUS_ACTIVE")).toBeInTheDocument();
    expect(screen.getByText("周额度")).toBeInTheDocument();
    expect(screen.getByText("高频任务额度")).toBeInTheDocument();
    expect(screen.getByText(/200 \/ 1,000 点数/)).toBeInTheDocument();
    expect(screen.getByText(/2 \/ 20 次/)).toBeInTheDocument();
    expect(screen.getByText("GOOGLE ONE AI 可用余额")).toBeInTheDocument();
    expect(screen.getByText("25,000 点数")).toBeInTheDocument();
    expect(screen.getByText("最低使用门槛：50")).toBeInTheDocument();
    expect(screen.getByText(/HTTP 503/)).toBeInTheDocument();
    expect(screen.getByText(/CLIProxyAPI 缓存|CLIProxyAPI cache/i)).toBeInTheDocument();
    expect(screen.getByText(/默认网关|Default gateway/i)).toBeInTheDocument();
    expect(screen.getByText("09/14 08:00")).toBeInTheDocument();
    expect(screen.getByText("09/14 08:05")).toBeInTheDocument();
  });
});
