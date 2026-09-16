import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountPoolCredentialsPanel } from "./AccountPoolCredentialsPanel";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";

const listCredentials = vi.fn();
const patchAuthFileStatus = vi.fn();
const getAuthFileRefreshStatus = vi.fn();
const refreshAuthFiles = vi.fn();
const setAuthFileRefreshInterval = vi.fn();
const uploadAuthFile = vi.fn();
const deleteAuthFile = vi.fn();

vi.mock("./AccountPoolManagementApi", async (importOriginal) => {
  const original = await importOriginal<typeof import("./AccountPoolManagementApi")>();
  return {
    ...original,
    listAccountPoolCredentials: (...args: unknown[]) => listCredentials(...args),
    patchAccountPoolAuthFileStatus: (...args: unknown[]) => patchAuthFileStatus(...args),
    getAccountPoolAuthFileRefreshStatus: (...args: unknown[]) => getAuthFileRefreshStatus(...args),
    refreshAccountPoolAuthFiles: (...args: unknown[]) => refreshAuthFiles(...args),
    setAccountPoolAuthFileRefreshInterval: (...args: unknown[]) => setAuthFileRefreshInterval(...args),
    uploadAccountPoolAuthFile: (...args: unknown[]) => uploadAuthFile(...args),
    deleteAccountPoolAuthFile: (...args: unknown[]) => deleteAuthFile(...args),
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
        account_email: "test-account@example.test",
        account_id: "account-identity-123",
        file_name: "test-account.json",
      },
    ]);
    patchAuthFileStatus.mockResolvedValue(environment);
    uploadAuthFile.mockResolvedValue(environment);
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

  it("identifies the account behind a card without exposing credential contents", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AccountPoolCredentialsPanel accessToken="token" environments={[environment]} />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("test-account@example.test")).toBeInTheDocument();
    expect(screen.getByText("account-identity-123")).toBeInTheDocument();
    expect(screen.getByText("test-account.json")).toBeInTheDocument();
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

  it("replaces the selected card's file without a separate delete request", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");
    render(
      <QueryClientProvider client={queryClient}>
        <AccountPoolCredentialsPanel
          accessToken="token"
          environments={[{ ...environment, id: "other-card", name: "Other card" }, environment]}
        />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: /Replace file|更换文件/i }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByRole("combobox", { name: /Target card|目标卡片/i })).toHaveValue(environment.id);
    expect(dialog.getByRole("combobox", { name: /Target card|目标卡片/i })).toBeDisabled();
    expect(dialog.getByText(/Current file: test-account.json|当前文件：test-account.json/)).toBeVisible();
    expect(dialog.getByRole("button", { name: /Confirm replacement|确认更换/i })).toBeDisabled();
    const file = new File(['{"refresh_token":"test-replacement"}'], "replacement.json", { type: "application/json" });
    await user.upload(dialog.getByLabelText(/Auth file|认证文件/i), file);
    await user.click(dialog.getByRole("button", { name: /Confirm replacement|确认更换/i }));

    await waitFor(() => expect(uploadAuthFile).toHaveBeenCalledWith("token", environment.id, file));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(deleteAuthFile).not.toHaveBeenCalled();
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["account-pool", "credentials", "token"] });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["account-pool", "environments"] });
  });

  it("discards the selected file on cancel and allows regular uploads to choose a card", async () => {
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AccountPoolCredentialsPanel accessToken="token" environments={[environment]} />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: /Replace file|更换文件/i }));
    await user.upload(screen.getByLabelText(/Auth file|认证文件/i), new File(["{}"], "cancelled.json"));
    await user.click(screen.getByRole("button", { name: /Cancel|取消/i }));
    await user.click(screen.getByRole("button", { name: /Replace file|更换文件/i }));
    expect(screen.getByLabelText(/Auth file|认证文件/i)).toHaveValue("");
    expect(screen.getByRole("button", { name: /Confirm replacement|确认更换/i })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Cancel|取消/i }));
    await user.click(screen.getByRole("button", { name: /Upload auth file|上传认证文件/i }));
    expect(within(screen.getByRole("dialog")).getByRole("combobox", { name: /Target card|目标卡片/i })).toBeEnabled();
    expect(uploadAuthFile).not.toHaveBeenCalled();
    expect(deleteAuthFile).not.toHaveBeenCalled();
  });

  it("locks the form while replacing and keeps failures open for retry with refreshed card state", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");
    const pending = Promise.withResolvers<AccountPoolEnvironment>();
    uploadAuthFile.mockReturnValueOnce(pending.promise);
    render(
      <QueryClientProvider client={queryClient}>
        <AccountPoolCredentialsPanel accessToken="token" environments={[environment]} />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: /Replace file|更换文件/i }));
    const file = new File(["{}"], "retry.json", { type: "application/json" });
    await user.upload(screen.getByLabelText(/Auth file|认证文件/i), file);
    await user.click(screen.getByRole("button", { name: /Confirm replacement|确认更换/i }));
    expect(screen.getByLabelText(/Auth file|认证文件/i)).toBeDisabled();
    expect(screen.getByRole("button", { name: /Cancel|取消/i })).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeVisible();
    pending.reject(new Error("Validation failed"));

    expect(await within(screen.getByRole("dialog")).findByRole("alert")).toHaveTextContent(/retry|重试/i);
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["account-pool", "environments"] });
    expect(screen.getByLabelText(/Auth file|认证文件/i)).toBeEnabled();
    await user.click(screen.getByRole("button", { name: /Confirm replacement|确认更换/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(uploadAuthFile).toHaveBeenCalledTimes(2);
    expect(uploadAuthFile).toHaveBeenLastCalledWith("token", environment.id, file);
    expect(deleteAuthFile).not.toHaveBeenCalled();
  });
});
