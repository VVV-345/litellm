import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import i18n from "@/i18n";
import { RuntimePolicyDialog } from "./RuntimePolicyDialog";
import type { AccountPolicy, PolicyView } from "@/features/account-pool/api/AccountPoolManagementApi";
import type { AccountPoolEnvironment } from "@/features/account-pool/utils/AccountPoolTypes";
import { accountPoolQueryKeys } from "@/features/account-pool/hooks/accountPoolQueryKeys";

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
    group: "",
    tags: [],
    excluded_models: [],
    model_aliases: [],
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
  it("adopts a refreshed policy when there are no unsaved changes", async () => {
    const pending = Promise.withResolvers<PolicyView>();
    getPolicy.mockReturnValue(pending.promise);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { unmount } = render(<QueryClientProvider client={client}><RuntimePolicyDialog accessToken="token" environment={environment} environments={[environment]} policies={[policyView]} onOpenRuntimeConfig={vi.fn()} onClose={vi.fn()} /></QueryClientProvider>);
    await act(async () => { pending.resolve({ ...policyView, version: 4, policy: { ...policyView.policy!, routing: { ...policyView.policy!.routing!, quota_reserve_5h_percent: 42 } } }); });
    await waitFor(() => expect(screen.getByRole("spinbutton", { name: "5 小时额度保留比例 (%)" })).toHaveValue(42));
    fireEvent.click(screen.getByRole("button", { name: /保存配置|Save configuration/i }));
    await waitFor(() => expect(savePolicy).toHaveBeenCalledOnce());
    expect(savePolicy.mock.calls[0][2]).toBe(4);
    unmount();
    client.clear();
  });

  it("shows list-cached policy immediately and preserves edited fields and their version after refresh", async () => {
    const pending = Promise.withResolvers<PolicyView>();
    getPolicy.mockReturnValue(pending.promise);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RuntimePolicyDialog accessToken="token" environment={environment} environments={[environment]} policies={[policyView]} onOpenRuntimeConfig={vi.fn()} onClose={vi.fn()} /></QueryClientProvider>);
    const field = screen.getByRole("spinbutton", { name: "5 小时额度保留比例 (%)" });
    fireEvent.change(field, { target: { value: "17" } });
    await act(async () => pending.resolve({ ...policyView, version: 4, policy: { ...policyView.policy!, routing: { ...policyView.policy!.routing!, quota_reserve_5h_percent: 42 } } }));
    await waitFor(() => expect(client.getQueryData<PolicyView>(accountPoolQueryKeys.policy("token", environment.id))?.version).toBe(4));
    expect(screen.getByRole("spinbutton", { name: "5 小时额度保留比例 (%)" })).toHaveValue(17);
    fireEvent.click(screen.getByRole("button", { name: /保存配置|Save configuration/i }));
    await waitFor(() => expect(savePolicy).toHaveBeenCalledTimes(1));
    expect(savePolicy.mock.calls[0][2]).toBe(3);
    expect(savePolicy.mock.calls[0][3].routing.quota_reserve_5h_percent).toBe(17);
    client.clear();
  });

  it("keeps saved fields and version when an older background refresh finishes later", async () => {
    const pending = Promise.withResolvers<PolicyView>();
    getPolicy.mockReturnValue(pending.promise);
    savePolicy.mockResolvedValue({
      ...policyView,
      version: 4,
      policy: { ...policyView.policy!, routing: { ...policyView.policy!.routing!, quota_reserve_5h_percent: 17 } },
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { unmount } = render(
      <QueryClientProvider client={client}>
        <RuntimePolicyDialog accessToken="token" environment={environment} environments={[environment]} policies={[policyView]} onOpenRuntimeConfig={vi.fn()} onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    fireEvent.change(screen.getByRole("spinbutton", { name: "5 小时额度保留比例 (%)" }), { target: { value: "17" } });
    fireEvent.click(screen.getByRole("button", { name: /保存配置|Save configuration/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /保存配置|Save configuration/i })).toBeEnabled());
    await act(async () => pending.resolve(policyView));
    expect(screen.getByRole("spinbutton", { name: "5 小时额度保留比例 (%)" })).toHaveValue(17);
    expect(client.getQueryData<PolicyView>(accountPoolQueryKeys.policy("token", environment.id))?.version).toBe(4);
    fireEvent.click(screen.getByRole("button", { name: /保存配置|Save configuration/i }));
    await waitFor(() => expect(savePolicy).toHaveBeenCalledTimes(2));
    expect(savePolicy.mock.calls[1][2]).toBe(4);
    unmount();
    client.clear();
  });

  it("saves separate reserve windows and controlled passthrough without changing inherited protocols", async () => {
    renderDialog();
    fireEvent.change(await screen.findByRole("spinbutton", { name: "5 小时额度保留比例 (%)" }), { target: { value: "10" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "周额度保留比例 (%)" }), { target: { value: "25" } });
    fireEvent.click(screen.getByRole("switch", { name: "开启协议透传" }));
    fireEvent.click(screen.getByRole("button", { name: /保存配置|Save configuration/i }));
    await waitFor(() => expect(savePolicy).toHaveBeenCalledTimes(1));
    const saved = savePolicy.mock.calls[0][3] as AccountPolicy;
    expect(saved.routing).toMatchObject({ quota_reserve_5h_percent: 10, quota_reserve_weekly_percent: 25 });
    expect(saved.transport).toMatchObject({ supported_protocols: null, passthrough_enabled: true });
  });

  it("saves an explicit empty protocol set instead of silently restoring defaults", async () => {
    renderDialog();
    fireEvent.click(await screen.findByRole("switch", { name: "继承默认协议" }));
    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
    fireEvent.click(screen.getByRole("button", { name: /保存配置|Save configuration/i }));
    await waitFor(() => expect(savePolicy).toHaveBeenCalledTimes(1));
    expect((savePolicy.mock.calls[0][3] as AccountPolicy).transport?.supported_protocols).toEqual([]);
  });

  it("saves a separate read timeout without changing the total timeout or routing", async () => {
    renderDialog();
    const readTimeout = await screen.findByRole("spinbutton", { name: /读取空闲超时|Read idle timeout/i });
    fireEvent.change(readTimeout, { target: { value: "60" } });
    fireEvent.click(screen.getByRole("button", { name: /保存配置|Save configuration/i }));
    await waitFor(() => expect(savePolicy).toHaveBeenCalledTimes(1));
    const saved = savePolicy.mock.calls[0][3] as AccountPolicy;
    expect(saved.transport?.read_timeout_seconds).toBe(60);
    expect(saved.transport?.request_timeout_seconds).toBe(120);
    expect(saved.routing?.strategy).toBe("plan");
  });

  beforeEach(() => {
    void i18n.changeLanguage("zh-CN");
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
