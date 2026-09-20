import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RuntimePolicyDialog } from "./RuntimePolicyDialog";
import type { AccountPolicy, PolicyView } from "@/features/account-pool/api/AccountPoolManagementApi";
import type { AccountPoolEnvironment } from "@/features/account-pool/utils/AccountPoolTypes";

const getPolicy = vi.fn();
const savePolicy = vi.fn();

vi.mock("@/features/account-pool/api/AccountPoolManagementApi", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/features/account-pool/api/AccountPoolManagementApi")>();
  return {
    ...original,
    getAccountPolicy: (...args: unknown[]) => getPolicy(...args),
    saveAccountPolicy: (...args: unknown[]) => savePolicy(...args),
  };
});

const environment = {
  id: "00000000-0000-4000-8000-000000000001",
  version: 1,
  name: "Codex 主账号",
  provider: "openai",
  channel: "cliproxyapi",
  supplier: "openai_codex",
  authorization_flow: "browser_oauth",
  status: "ready",
  configuration_pending: false,
  desired_configuration_version: 1,
  observed_configuration_version: 1,
  enabled: true,
  manual_cooldown: false,
  concurrency_limit: 2,
  proxy_mode: "profile",
  proxy_profile_id: "clash-gateway-7891",
  available_models: ["gpt-5"],
  enabled_models: ["gpt-5"],
  quota: {
    observed_at: "2026-09-15T13:00:00Z",
    refresh_attempted_at: "2026-09-15T13:05:00Z",
    source: "cliproxyapi_cache",
    plan_type: "plus",
    refresh_status: "failed",
    refresh_error: "provider quota endpoint rejected the request",
    windows: [
      {
        name: "5 hour",
        used_percent: 69,
        remaining_percent: 31,
        window_minutes: 300,
        resets_at: "2090-09-15T18:00:00Z",
      },
    ],
    balances: [],
  },
  model_quotas: [],
  cooldown_until: null,
  automatic_cooldown: false,
  last_error: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
} as AccountPoolEnvironment;

const policyView = {
  card_id: environment.id,
  version: 3,
  policy: {
    routing: { strategy: "plan" },
    transport: { websocket: "enabled", debug_log_enabled: true },
  },
  runtime_status: "failed",
  runtime_error: "runtime sync failed",
  capabilities: [],
  metadata_status: "saved",
} as unknown as PolicyView;

const renderDialog = (environments: AccountPoolEnvironment[] = [environment]) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RuntimePolicyDialog
        accessToken="token"
        environment={environment}
        environments={environments}
        policies={[]}
        onOpenRuntimeConfig={vi.fn()}
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  );

describe("RuntimePolicyDialog", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    getPolicy.mockResolvedValue(policyView);
    savePolicy.mockResolvedValue(policyView);
  });

  it("preserves same-card attempts and preferred fallback cards independently of the fallback switch", async () => {
    const user = userEvent.setup();
    const backup = { ...environment, id: "00000000-0000-4000-8000-000000000002", name: "Backup" };
    getPolicy.mockResolvedValue({
      ...policyView,
      policy: {
        ...policyView.policy,
        routing: { strategy: "priority", max_attempts: 5, fallback_enabled: false, preferred_account_ids: [backup.id] },
      },
    });
    renderDialog([environment, backup]);
    await screen.findByText(/卡片之间的权重/);
    expect(screen.queryByRole("switch", { name: /故障切换|Failover/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /保存配置|Save configuration/i }));
    await waitFor(() => expect(savePolicy).toHaveBeenCalledTimes(1));
    expect((savePolicy.mock.calls[0][3] as AccountPolicy).routing).toMatchObject({
      max_attempts: 5,
      fallback_enabled: false,
      preferred_account_ids: [backup.id],
    });
  });

  it("shows runtime failures and preserves supported routing and transport settings", async () => {
    const user = userEvent.setup();
    renderDialog();

    expect(await screen.findByRole("alert")).toHaveTextContent("runtime sync failed");
    expect(screen.getByRole("switch", { name: /调试日志|Debug logging/i })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /^WebSocket$/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /保存配置|Save configuration/i }));
    await waitFor(() => expect(savePolicy).toHaveBeenCalledTimes(1));
    const saved = savePolicy.mock.calls[0][3] as AccountPolicy;

    expect(saved.routing?.strategy).toBe("plan");
    expect(saved.transport?.websocket).toBe("enabled");
    expect(saved.transport?.debug_log_enabled).toBe(true);
  });

  it("explains routing and transport settings in user-facing language", async () => {
    renderDialog();

    expect(await screen.findByText(/卡片之间的权重/)).toBeInTheDocument();
    expect(screen.getByText(/重试次数和退避使用 LiteLLM/)).toBeInTheDocument();
    expect(screen.getByText(/剩余额度达到或低于|remaining quota reaches/i)).toBeInTheDocument();
    expect(screen.getByText(/认证信息与敏感内容仍会脱敏|credentials and sensitive data redacted/i)).toBeInTheDocument();
  });

  it("shows the same quota detail and route diagnostics as the dashboard card", async () => {
    renderDialog();

    expect(await screen.findByRole("meter", { name: /5 小时额度|5-hour allowance/i })).toHaveAttribute(
      "aria-valuenow",
      "31",
    );
    expect(screen.getByText(/CLIProxyAPI 缓存|CLIProxyAPI cache/i)).toBeInTheDocument();
    expect(screen.getByText(/Clash 端口 7891|Clash port 7891/i)).toBeInTheDocument();
    expect(screen.getByText(/provider quota endpoint rejected/i)).toBeInTheDocument();
  });
});
