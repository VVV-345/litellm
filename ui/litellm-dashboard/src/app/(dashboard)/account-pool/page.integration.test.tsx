import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import AccountPoolPage from "./page";
import type { AccountPoolEnvironment } from "@/features/account-pool/utils/AccountPoolTypes";
import i18n from "@/i18n";
import { preloadAccountPoolModules } from "@/features/account-pool/preloadModules";
import { accountPoolQueryKeys } from "@/features/account-pool/hooks/accountPoolQueryKeys";

const push = vi.fn();
const listAccounts = vi.fn();
const updateAccount = vi.fn();
const searchState = { value: "" };
const loadStats = vi.fn();
vi.mock("@/features/account-pool/api/AccountPoolAutomationApi", async (original) => ({
  ...(await original<typeof import("@/features/account-pool/api/AccountPoolAutomationApi")>()),
  getAutomation: async () => ({ version: 1, settings: { enabled: false }, profiles: [], jobs: [], image_checked: false }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => new URLSearchParams(searchState.value),
}));
vi.mock("@/app/(dashboard)/hooks/useAuthorized", () => ({
  default: () => ({ accessToken: "token", userRole: "Admin", isViewOnly: false }),
}));
vi.mock("@/features/account-pool/api/AccountPoolApi", async (original) => ({
  ...(await original<typeof import("@/features/account-pool/api/AccountPoolApi")>()),
  listAccountPoolEnvironments: (...args: unknown[]) => listAccounts(...args),
  updateAccountPoolEnvironment: (...args: unknown[]) => updateAccount(...args),
  listAccountPoolProxyGateways: async () => [],
  listAccountPoolProxyProfiles: async () => [],
}));
vi.mock("@/features/account-pool/api/AccountPoolManagementApi", async (original) => ({
  ...(await original<typeof import("@/features/account-pool/api/AccountPoolManagementApi")>()),
  listAccountPolicies: async () => [],
  getAccountPoolQuotaRefreshStatus: async () => ({ running: false }),
  getAccountPoolDashboardStats: (...args: unknown[]) => loadStats(...args),
  getAccountPolicy: async () => ({ card_id: environment.id, version: 3, policy: {}, capabilities: [] }),
}));

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

const renderPage = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}>
      <AccountPoolPage />
    </QueryClientProvider>,
  );

describe("account card local configuration", () => {
  beforeAll(async () => {
    await preloadAccountPoolModules();
  });

  beforeEach(async () => {
    await i18n.changeLanguage("zh-CN");
    vi.clearAllMocks();
    searchState.value = "";
    listAccounts.mockResolvedValue([environment]);
    loadStats.mockResolvedValue({ cards: [], summary: {} });
    updateAccount.mockImplementation(async (_token, _id, payload) => ({ ...environment, ...payload }));
  });
  afterEach(() => vi.useRealTimers());

  it("keeps feedback outside the account pool and ignores its former tab URL", async () => {
    searchState.value = "tab=feedback";
    renderPage();

    expect(screen.queryByRole("tab", { name: "反馈中心" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "仪表盘" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByTestId(`account-pool-card-${environment.id}`)).toBeInTheDocument();
  });

  it("renders cached cards immediately on return and keeps them after a failed background refresh", async () => {
    const pending = Promise.withResolvers<AccountPoolEnvironment[]>();
    listAccounts.mockReturnValue(pending.promise);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    client.setQueryData(accountPoolQueryKeys.environments("token"), [environment], { updatedAt: 1 });
    const { unmount } = render(
      <QueryClientProvider client={client}>
        <AccountPoolPage />
      </QueryClientProvider>,
    );
    const card = `account-pool-card-${environment.id}`;
    expect(screen.getByTestId(card)).toBeVisible();
    await waitFor(() => expect(listAccounts).toHaveBeenCalledOnce());
    await act(async () => pending.reject(new Error("slow request failed")));
    await waitFor(() => expect(client.getQueryState(accountPoolQueryKeys.environments("token"))?.status).toBe("error"));
    expect(screen.getByTestId(card)).toBeVisible();
    expect(screen.getByText("卡片刷新失败，仍显示上次数据。")).toBeVisible();
    unmount();
    client.clear();
  });

  it("opens the single automation workspace from OAuth with the selected blank card", async () => {
    const user = userEvent.setup();
    searchState.value = "tab=oauth";
    listAccounts.mockResolvedValue([{ ...environment, status: "awaiting_authorization", proxy_profile_id: "proxy-test" }]);
    renderPage();
    await user.click(await screen.findByRole("button", { name: `自动化授权 ${environment.name}` }));
    expect(screen.getByRole("tab", { name: "自动化上号", selected: true })).toBeVisible();
    expect(screen.getByRole("tab", { name: "2FA / OAuth 授权", selected: true })).toBeVisible();
    await screen.findByRole("option", { name: environment.name });
    expect(screen.getByRole("combobox", { name: "空白 Codex 卡片" })).toHaveValue(environment.id);
    expect(screen.getByRole("textbox", { name: "单账号资料" })).toHaveValue("");
  });

  it("updates an open model test when query refresh removes access proof, and closes it after card deletion", async () => {
    const user = userEvent.setup();
    const fallbackCard: AccountPoolEnvironment = {
      ...environment,
      access_token_fallback: {
        enabled: true,
        active: true,
        failure_source: "refresh",
        reason: "temporary_access",
        expires_at: "2090-01-01T00:00:00Z",
        verified_models: environment.enabled_models,
      },
    };
    listAccounts.mockResolvedValue([fallbackCard]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const { unmount } = render(
      <QueryClientProvider client={client}>
        <AccountPoolPage />
      </QueryClientProvider>,
    );
    await user.click(await screen.findByRole("button", { name: `测试 ${environment.name} 的模型连接` }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "开始测试" })).toBeEnabled();
    listAccounts.mockResolvedValue([
      {
        ...fallbackCard,
        access_token_fallback: { ...fallbackCard.access_token_fallback, verified_models: [] },
      },
    ]);
    await act(() => client.refetchQueries({ queryKey: accountPoolQueryKeys.environments("token") }));
    expect(listAccounts).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "开始测试" })).toBeDisabled());
    listAccounts.mockResolvedValue([]);
    await act(() => client.refetchQueries({ queryKey: accountPoolQueryKeys.environments("token") }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    unmount();
    client.clear();
  });

  it("refreshes visible dashboard statistics without polling inactive tabs", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const page = () => (
      <QueryClientProvider client={client}>
        <AccountPoolPage />
      </QueryClientProvider>
    );
    const { rerender, unmount } = render(page());
    await screen.findByTestId(`account-pool-card-${environment.id}`);
    searchState.value = "tab=oauth";
    rerender(page());
    vi.useFakeTimers();
    searchState.value = "tab=dashboard";
    rerender(page());
    await act(() => vi.advanceTimersByTimeAsync(15000));
    expect(loadStats).toHaveBeenCalledTimes(2);
    searchState.value = "tab=oauth";
    rerender(page());
    await act(() => vi.advanceTimersByTimeAsync(30000));
    expect(loadStats).toHaveBeenCalledTimes(2);
    unmount();
    client.clear();
  });

  it("does not load card data on a tab that does not consume it, then loads it on return", async () => {
    searchState.value = "tab=upstream-sync";
    const { rerender } = renderPage();
    expect(screen.getByRole("tab", { name: "上游更新" })).toHaveAttribute("aria-selected", "true");
    expect(listAccounts).not.toHaveBeenCalled();
    searchState.value = "tab=dashboard";
    rerender(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}>
        <AccountPoolPage />
      </QueryClientProvider>,
    );
    expect(await screen.findByTestId(`account-pool-card-${environment.id}`)).toBeInTheDocument();
    expect(listAccounts).toHaveBeenCalledTimes(1);
  });

  it.each(["oauth", "quotas"])(
    "keeps retired %s links usable without restoring duplicate navigation",
    async (legacy) => {
      searchState.value = `tab=${legacy}`;
      renderPage();
      expect(screen.queryByRole("tab", { name: "配额管理" })).not.toBeInTheDocument();
      expect(screen.queryByRole("tab", { name: "OAuth 授权" })).not.toBeInTheDocument();
      if (legacy === "oauth") {
        expect(screen.getByRole("tab", { name: /提供商与 OAuth/ })).toHaveAttribute("aria-selected", "true");
        expect(screen.getByRole("tab", { name: /OAuth 登录/ })).toHaveAttribute("aria-selected", "true");
        expect(await screen.findByText(environment.name)).toBeVisible();
        expect(screen.queryByTestId(`account-pool-card-${environment.id}`)).not.toBeInTheDocument();
      } else {
        expect(screen.getByRole("tab", { name: "仪表盘" })).toHaveAttribute("aria-selected", "true");
        expect(await screen.findByRole("combobox", { name: "自动刷新档位" })).toBeVisible();
      }
    },
  );

  it("opens the authorization subtab from the old URL without a duplicate top-level module", () => {
    searchState.value = "tab=oauth-automation";
    renderPage();
    expect(screen.getByRole("tab", { name: "自动化上号", selected: true })).toBeVisible();
    expect(screen.getByRole("tab", { name: "CPA 文件导入" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "Sub2API 文件导入" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "2FA / OAuth 授权", selected: true })).toBeVisible();
    expect(screen.queryByRole("tab", { name: "授权自动化" })).not.toBeInTheDocument();
  });

  it("opens runtime configuration from the card and saves without navigation", async () => {
    const user = userEvent.setup();
    renderPage();
    const tabs = screen.getAllByRole("tab");
    const onboardingIndex = tabs.findIndex((tab) => tab.textContent === "自动化上号");
    expect(onboardingIndex).toBe(3);
    expect(tabs[onboardingIndex - 1]).toHaveTextContent(/认证文件|Credentials/i);
    await user.click(await screen.findByRole("button", { name: `配置 ${environment.name}` }));
    fireEvent.change(await screen.findByDisplayValue(environment.name), { target: { value: "Updated card" } });
    await user.click(await screen.findByRole("button", { name: /保存配置|Save configuration/i }));
    await waitFor(() =>
      expect(updateAccount).toHaveBeenCalledWith(
        "token",
        environment.id,
        expect.objectContaining({
          name: "Updated card",
          enabled_models: environment.enabled_models,
          proxy_mode: "default_gateway",
        }),
      ),
    );
  });
});

it("passes the selected statistics range to the request client", async () => {
  const user = userEvent.setup();
  renderPage();
  await user.click(await screen.findByRole("combobox", { name: "统计天数" }));
  await user.click(await screen.findByRole("option", { name: "近 7 天" }));
  await waitFor(() => expect(loadStats).toHaveBeenCalledWith("token", 7));
});
