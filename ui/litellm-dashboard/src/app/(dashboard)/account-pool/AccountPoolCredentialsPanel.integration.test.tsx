import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountPoolCredentialsPanel } from "./AccountPoolCredentialsPanel";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";

const listCredentials = vi.fn();
const patchAuthFileStatus = vi.fn();
const getAuthFileRefreshStatus = vi.fn();
const refreshAuthFiles = vi.fn();
const setAuthFileRefreshInterval = vi.fn();

vi.mock("./AccountPoolManagementApi", async (importOriginal) => {
  const original = await importOriginal<typeof import("./AccountPoolManagementApi")>();
  return {
    ...original,
    listAccountPoolCredentials: (...args: unknown[]) => listCredentials(...args),
    patchAccountPoolAuthFileStatus: (...args: unknown[]) => patchAuthFileStatus(...args),
    getAccountPoolAuthFileRefreshStatus: (...args: unknown[]) => getAuthFileRefreshStatus(...args),
    refreshAccountPoolAuthFiles: (...args: unknown[]) => refreshAuthFiles(...args),
    setAccountPoolAuthFileRefreshInterval: (...args: unknown[]) => setAuthFileRefreshInterval(...args),
  };
});

const environment = {
  id: "00000000-0000-4000-8000-000000000002",
  version: 4,
  name: "plus02",
  provider: "openai",
  channel: "cliproxyapi",
  supplier: "openai_codex",
  authorization_flow: "browser_oauth",
  status: "cooling_down",
  desired_configuration_version: 0,
  observed_configuration_version: 0,
  configuration_pending: false,
  enabled: true,
  manual_cooldown: false,
  concurrency_limit: 2,
  proxy_mode: "default_gateway",
  proxy_profile_id: null,
  available_models: [],
  enabled_models: [],
  quota: { observed_at: null, plan_type: "plus", windows: [], balances: [] },
  model_quotas: [],
  cooldown_until: null,
  automatic_cooldown: true,
  last_error: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
} as AccountPoolEnvironment;

describe("AccountPoolCredentialsPanel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    listCredentials.mockResolvedValue([
      {
        id: `${environment.id}:default`,
        card_id: environment.id,
        card_name: environment.name,
        supplier: environment.supplier,
        kind: "oauth_file",
        status: "disabled",
        enabled: false,
        model_count: 0,
        auth_index: null,
      },
    ]);
    patchAuthFileStatus.mockResolvedValue(environment);
    getAuthFileRefreshStatus.mockResolvedValue({
      interval_minutes: 15,
      running: true,
      last_started_at: "2026-09-16T10:00:00Z",
      last_completed_at: "2026-09-16T10:01:00Z",
      next_refresh_at: "2026-09-16T10:16:00Z",
      last_failed_count: null,
    });
    refreshAuthFiles.mockResolvedValue({
      interval_minutes: 15,
      running: false,
      last_started_at: "2026-09-16T10:00:00Z",
      last_completed_at: "2026-09-16T10:01:00Z",
      next_refresh_at: "2026-09-16T10:16:00Z",
      last_failed_count: 0,
    });
    setAuthFileRefreshInterval.mockResolvedValue({
      interval_minutes: 30,
      running: false,
      last_started_at: "2026-09-16T10:00:00Z",
      last_completed_at: "2026-09-16T10:01:00Z",
      next_refresh_at: "2026-09-16T10:31:00Z",
      last_failed_count: null,
    });
  });

  it("shows and updates authentication refresh controls", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");
    render(
      <QueryClientProvider client={queryClient}>
        <AccountPoolCredentialsPanel accessToken="token" environments={[environment]} />
      </QueryClientProvider>,
    );

    expect(
      await screen.findByRole("combobox", { name: /Authentication refresh interval|认证刷新档位/i }),
    ).toHaveTextContent(/15/);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /Refresh authentication files|刷新认证文件/i }).querySelector("svg"),
      ).toHaveClass("animate-spin"),
    );
    await user.click(screen.getByRole("combobox", { name: /Authentication refresh interval|认证刷新档位/i }));
    expect(await screen.findByRole("option", { name: /Every 5 minutes|每 5 分钟/i })).toBeVisible();
    expect(screen.getByRole("option", { name: /Every 15 minutes|每 15 分钟/i })).toBeVisible();
    expect(screen.getByRole("option", { name: /Every 30 minutes|每 30 分钟/i })).toBeVisible();
    expect(screen.getByRole("option", { name: /Every 60 minutes|每 60 分钟/i })).toBeVisible();
    await user.click(screen.getByRole("option", { name: /Every 30 minutes|每 30 分钟/i }));

    await waitFor(() => expect(setAuthFileRefreshInterval).toHaveBeenCalledWith("token", 30));
    await user.click(screen.getByRole("button", { name: /Refresh authentication files|刷新认证文件/i }));

    await waitFor(() => expect(refreshAuthFiles).toHaveBeenCalledWith("token"));
    await waitFor(() =>
      expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["account-pool", "auth-file-refresh", "token"] }),
    );
    expect(screen.getByText(/Last completed refresh|上次完成刷新/i).parentElement).toHaveTextContent(/09\/16/);
    expect(screen.getByText(/Next scheduled refresh|下次计划刷新/i).parentElement).toHaveTextContent(/09\/16/);
  });

  it("enables a disabled auth file even when its card remains enabled", async () => {
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AccountPoolCredentialsPanel accessToken="token" environments={[environment]} />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: /启用|Enable/i }));

    await waitFor(() => expect(patchAuthFileStatus).toHaveBeenCalledWith("token", environment.id, false));
  });
});
