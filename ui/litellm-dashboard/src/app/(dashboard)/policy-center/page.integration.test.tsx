import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PolicyCenterPage from "./page";

const getSettings = vi.fn();
const updateSettings = vi.fn();
const resetPolicy = vi.fn();
const listPolicies = vi.fn();
const setCallbacks = vi.fn();

const cardInheriting = {
  id: "00000000-0000-4000-8000-000000000001",
  name: "继承卡片",
  enabled_models: ["gpt-5"],
};

const cardIndependent = {
  id: "00000000-0000-4000-8000-000000000002",
  name: "独立卡片",
  enabled_models: ["gpt-5"],
};

vi.mock("@/app/(dashboard)/hooks/useAuthorized", () => ({
  default: () => ({ accessToken: "token", userID: "user", userRole: "admin" }),
}));

vi.mock("@/features/account-pool/hooks/useAccountPoolQuery", () => ({
  useAccountPoolQuery: () => ({ data: [cardInheriting, cardIndependent], isLoading: false }),
}));

vi.mock("@/features/account-pool/hooks/accountPoolOptions", () => ({
  accountPoolPolicyOptions: () => ({
    queryKey: ["account-pool", "policies", "token"],
    queryFn: () => listPolicies(),
  }),
}));

vi.mock("@/features/account-pool/api/AccountPoolManagementApi", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/features/account-pool/api/AccountPoolManagementApi")>();
  return {
    ...original,
    getAccountPoolSettings: (...args: unknown[]) => getSettings(...args),
    updateAccountPoolSettings: (...args: unknown[]) => updateSettings(...args),
    resetAccountPolicy: (...args: unknown[]) => resetPolicy(...args),
  };
});

vi.mock("@/components/networking", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/components/networking")>();
  return {
    ...original,
    getCallbacksCall: () =>
      Promise.resolve({ router_settings: { account_pool_routing: { selection: "native" } } }),
    setCallbacksCall: (...args: unknown[]) => {
      setCallbacks(...args);
      return Promise.resolve({});
    },
  };
});

vi.mock("@/components/Settings/RuntimeSettings/RuntimePolicyDialog", () => ({
  RuntimePolicyDialog: ({ environment }: { environment: { name: string } }) => (
    <div data-testid="policy-dialog">{environment.name}</div>
  ),
}));

vi.mock("@/components/Settings/RuntimeSettings/RuntimeConfigDialog", () => ({
  default: ({ environment }: { environment: { name: string } }) => (
    <div data-testid="runtime-config-dialog">{environment.name}</div>
  ),
}));

vi.mock("@/components/Settings/RuntimeSettings/RuntimeSettings", () => ({
  default: ({ category }: { category: string }) => <div data-testid={`runtime-settings-${category}`} />,
}));

vi.mock("@/lib/toast", () => ({
  toast: { success: vi.fn(), error: vi.fn(), fromError: vi.fn() },
}));

const settingsView = {
  version: 4,
  values: {
    default_route: "auto",
    default_concurrency_limit: 1,
    max_attempts: 3,
    request_timeout_seconds: 120,
    quota_reserve_percent: 75,
    quota_reserve_5h_percent: null,
    quota_reserve_weekly_percent: null,
    quota_snapshot_max_age: 600,
  },
  requires_reload: false,
};

beforeEach(() => {
  getSettings.mockResolvedValue(settingsView);
  listPolicies.mockResolvedValue([
    { card_id: cardIndependent.id, version: 2, policy: { routing: { strategy: "plan" } } },
  ]);
  updateSettings.mockResolvedValue(settingsView);
  resetPolicy.mockResolvedValue({ card_id: cardIndependent.id, version: 0, policy: {} });
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

const renderPage = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PolicyCenterPage />
    </QueryClientProvider>,
  );
};

describe("policy center", () => {
  it("marks inheriting and independent cards and only offers reset for the exception", async () => {
    renderPage();

    expect(await screen.findByText("继承全局")).toBeInTheDocument();
    expect(screen.getByText("独立配置 v2")).toBeInTheDocument();
    expect(screen.getByText("当前 1 张卡片继承全局，1 张卡片独立配置")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "回归全局" })).toHaveLength(1);
    expect(screen.getByTestId("runtime-settings-common")).toBeInTheDocument();
    expect(screen.getByTestId("runtime-settings-streaming")).toBeInTheDocument();
  });

  it("saves quota thresholds through the policy runtime section", async () => {
    renderPage();

    const reserve = await screen.findByLabelText("额度预留（%）");
    fireEvent.change(reserve, { target: { value: "80" } });
    fireEvent.click(screen.getByRole("button", { name: "保存卡内默认" }));

    await waitFor(() => expect(updateSettings).toHaveBeenCalled());
    expect(updateSettings).toHaveBeenCalledWith(
      "token",
      expect.objectContaining({
        version: 4,
        values: expect.objectContaining({ quota_reserve_percent: 80, quota_snapshot_max_age: 600 }),
      }),
      "policy",
    );
  });

  it("opens the independent policy editor from the card list", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "设为独立配置" }));

    expect(await screen.findByTestId("policy-dialog")).toHaveTextContent("继承卡片");
  });

  it("saves cross-card routing through the router settings payload", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("switch", { name: "同一会话优先使用同一卡片" }));
    fireEvent.click(screen.getByRole("button", { name: "保存跨卡调度" }));

    await waitFor(() => expect(setCallbacks).toHaveBeenCalled());
    expect(setCallbacks).toHaveBeenCalledWith("token", {
      router_settings: expect.objectContaining({
        account_pool_routing: expect.objectContaining({ session_affinity: false }),
      }),
    });
  });

  it("resets an independent card after confirmation", async () => {
    renderPage();

    const reset = await screen.findByRole("button", { name: "回归全局" });
    fireEvent.click(reset);

    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => expect(resetPolicy).toHaveBeenCalledWith("token", cardIndependent.id));
  });
});
