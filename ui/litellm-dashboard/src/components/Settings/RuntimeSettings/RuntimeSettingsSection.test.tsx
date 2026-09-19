/** 本文件验证流式子模块可打开、创建独立配置并在保存时提交卡片绑定。 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RuntimeSettingsSection } from "./RuntimeSettingsSection";
import type { AccountPoolSettings } from "@/app/(dashboard)/account-pool/AccountPoolManagementApi";
import type { AccountPoolEnvironment } from "@/app/(dashboard)/account-pool/AccountPoolTypes";

const getSettings = vi.fn();
const updateSettings = vi.fn();
const listProxyProfiles = vi.fn();
const toastSuccess = vi.fn();

vi.mock("@/app/(dashboard)/account-pool/AccountPoolManagementApi", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/app/(dashboard)/account-pool/AccountPoolManagementApi")>();
  return {
    ...original,
    getAccountPoolSettings: (...args: unknown[]) => getSettings(...args),
    updateAccountPoolSettings: (...args: unknown[]) => updateSettings(...args),
  };
});

vi.mock("@/app/(dashboard)/account-pool/AccountPoolApi", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/app/(dashboard)/account-pool/AccountPoolApi")>();
  return {
    ...original,
    listAccountPoolProxyProfiles: (...args: unknown[]) => listProxyProfiles(...args),
  };
});

vi.mock("@/lib/toast", () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccess(...args),
    error: vi.fn(),
    fromError: vi.fn(),
  },
}));

const settings: AccountPoolSettings = {
  access_profiles: [],
  advanced_profiles: [],
  common_profiles: [],
  debug_logging_enabled: false,
  default_concurrency_limit: 1,
  default_model_discovery: true,
  default_proxy_profile_id: null,
  default_route: "auto",
  error_logs_max_files: 10,
  auth_refresh_interval_minutes: 15,
  quota_refresh_interval_minutes: 5,
  full_log_success_enabled: true,
  full_log_sample_percent: 100,
  full_log_max_body_kb: 16384,
  full_log_max_storage_mb: 0,
  daily_log_max_rows: 0,
  log_redact_fields: [],
  runtime_log_level: "inherit",
  runtime_log_format: "inherit",
  runtime_log_console: true,
  runtime_log_file: false,
  runtime_log_max_mb: 100,
  runtime_log_backups: 5,
  runtime_log_stacktrace: true,
  runtime_log_quiet_dependencies: true,
  full_logging_enabled: false,
  full_log_skip_failed: false,
  daily_log_retention_days: 30,
  full_log_retention_days: 30,
  file_logging_enabled: false,
  force_model_prefix: false,
  logs_max_total_size_mb: 0,
  max_attempts: 1,
  max_retry_credentials: 1,
  max_retry_interval: 0,
  network_profiles: [],
  oauth_excluded_models: [],
  oauth_model_aliases: {},
  oauth_request_scoped_errors: {},
  payload: { default: [], "default-raw": [], override: [], "override-raw": [], filter: [] },
  payload_profiles: [],
  plugins_enabled: false,
  quota_profiles: [],
  quota_switch_preview_model: false,
  quota_switch_project: false,
  request_log_enabled: false,
  request_retry: 1,
  request_timeout_seconds: 120,
  streaming_enabled: true,
  streaming_profiles: [],
  streaming_rules: [],
  usage_statistics_enabled: false,
  websocket_auth_enabled: false,
  websocket_enabled: false,
};

const card = {
  id: "00000000-0000-4000-8000-000000000001",
  name: "Codex 主账号",
  supplier: "openai_codex",
} as AccountPoolEnvironment;

const reloadRequiredSettingsView = {
  version: 5,
  values: settings,
  requires_reload: true,
};

const renderPanel = (category: "streaming" | "network" | "quota" = "streaming") =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RuntimeSettingsSection accessToken="token" environments={[card]} category={category} />
    </QueryClientProvider>,
  );

describe("RuntimeSettingsSection", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    getSettings.mockResolvedValue({ version: 4, values: settings, requires_reload: false });
    updateSettings.mockResolvedValue({ version: 5, values: settings, requires_reload: false });
    listProxyProfiles.mockResolvedValue([]);
  });

  it("marks unsupported quota switching as inactive and preserves the old values", async () => {
    getSettings.mockResolvedValue({
      version: 4,
      values: { ...settings, quota_switch_project: true },
      requires_reload: false,
    });
    renderPanel("quota");
    expect(await screen.findByText(/目前不生效/)).toBeInTheDocument();
    expect(screen.getByText(/旧值开启，当前不执行/)).toBeInTheDocument();
    expect(screen.queryByRole("switch", { name: /切换项目/ })).not.toBeInTheDocument();
  });

  it("opens streaming settings and saves a named configuration with one selected card", async () => {
    const user = userEvent.setup();
    renderPanel();

    await screen.findByRole("switch", { name: /允许流式传输|Allow streaming/i });
    expect(screen.getByRole("switch", { name: /允许流式传输|Allow streaming/i })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: /新增配置|Add configuration/i }));
    await user.click(screen.getByRole("switch", { name: /继承全局配置|Inherit global configuration/i }));
    await user.click(screen.getByRole("combobox", { name: /选择要应用的卡片|Select a card to apply/i }));
    await user.click(await screen.findByRole("option", { name: "Codex 主账号" }));
    await user.click(screen.getAllByRole("button", { name: /保存配置|Save configuration/i })[0]);

    await waitFor(() => expect(updateSettings).toHaveBeenCalledTimes(1));
    const request = updateSettings.mock.calls[0][1] as { version: number; values: AccountPoolSettings };
    expect(request.version).toBe(4);
    expect(request.values.streaming_profiles).toEqual([
      expect.objectContaining({
        name: expect.stringMatching(/流式传输|Streaming/),
        card_ids: [card.id],
        inherit_global: false,
        values: { enabled: true },
      }),
    ]);
  });

  it("reports when saved settings require card runtimes to restart", async () => {
    const user = userEvent.setup();
    updateSettings.mockResolvedValue(reloadRequiredSettingsView);
    renderPanel();

    const saveButtons = await screen.findAllByRole("button", { name: /保存配置|Save configuration/i });
    await user.click(saveButtons[0]);

    await waitFor(() => expect(toastSuccess).toHaveBeenCalledTimes(1));
    expect(toastSuccess.mock.calls[0][0]).toMatch(/重启|Restart/i);
  });

  it("does not expose the unsupported WebSocket transport setting", async () => {
    const user = userEvent.setup();
    renderPanel("network");
    await screen.findAllByRole("button", { name: /保存配置|Save configuration/i });

    expect(screen.queryByRole("switch", { name: /启用 WebSocket|Enable WebSocket/i })).not.toBeInTheDocument();
  });
});
