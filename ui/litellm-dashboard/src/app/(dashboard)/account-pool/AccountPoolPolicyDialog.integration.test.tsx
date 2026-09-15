import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountPoolPolicyDialog } from "./AccountPoolPolicyDialog";
import type { AccountPolicy, PolicyView } from "./AccountPoolManagementApi";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";

const getPolicy = vi.fn();
const savePolicy = vi.fn();

vi.mock("./AccountPoolManagementApi", async (importOriginal) => {
  const original = await importOriginal<typeof import("./AccountPoolManagementApi")>();
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
  proxy_mode: "default_gateway",
  proxy_profile_id: null,
  available_models: ["gpt-5"],
  enabled_models: ["gpt-5"],
  quota: { observed_at: null, plan_type: null, windows: [], balances: [] },
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

const renderDialog = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AccountPoolPolicyDialog
        accessToken="token"
        environment={environment}
        environments={[environment]}
        policies={[]}
        onOpenRuntimeConfig={vi.fn()}
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  );

describe("AccountPoolPolicyDialog", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    getPolicy.mockResolvedValue(policyView);
    savePolicy.mockResolvedValue(policyView);
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
});
