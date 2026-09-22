import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import AccountPoolPage from "./page";
import type { AccountPoolEnvironment } from "@/features/account-pool/utils/AccountPoolTypes";
import i18n from "@/i18n";
import { preloadAccountPoolModules } from "@/features/account-pool/preloadModules";

const push = vi.fn();
const listAccounts = vi.fn();
const updateAccount = vi.fn();
const savePolicy = vi.fn();
const searchState = { value: "" };
const loadStats = vi.fn();
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
  saveAccountPolicy: (...args: unknown[]) => savePolicy(...args),
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
    savePolicy.mockResolvedValue({});
  });
  afterEach(() => vi.useRealTimers());

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

  it("opens the selected card policy, switches to runtime configuration and saves without navigation", async () => {
    const user = userEvent.setup();
    renderPage();
    const tabs = screen.getAllByRole("tab");
    const onboardingIndex = tabs.findIndex((tab) => tab.textContent === "自动化上号");
    expect(onboardingIndex).toBe(4);
    expect(tabs[onboardingIndex - 1]).toHaveTextContent(/认证文件|Credentials/i);
    fireEvent.doubleClick(await screen.findByTestId(`account-pool-card-${environment.id}`));
    expect(await screen.findByRole("dialog", {}, { timeout: 5000 })).toHaveTextContent(environment.name);
    await user.click(await screen.findByRole("button", { name: /保存配置|Save configuration/i }));
    await waitFor(() => expect(savePolicy).toHaveBeenCalledWith("token", environment.id, 3, expect.any(Object)));
    await user.click(screen.getByRole("button", { name: /打开运行配置|运行配置|runtime configuration/i }));
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
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(push).not.toHaveBeenCalled();
    expect(listAccounts.mock.calls.length).toBeGreaterThan(1);
  });
});
