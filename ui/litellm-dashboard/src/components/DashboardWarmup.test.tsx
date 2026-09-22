import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DashboardWarmup, { runDashboardWarmup, refreshWarmupQueries } from "./DashboardWarmup";
import { registerResponseCache } from "@/lib/http/responseCache";
import { registerAuthTokenGetter } from "@/lib/http/runtime";
import { initialLogsRange, requestLogsQueryOptions, DEFAULT_LOGS_SORTING } from "./view_logs/log_filter_logic";

vi.mock("@/app/(dashboard)/api-keys/page", () => ({}));
vi.mock("@/app/(dashboard)/models-and-endpoints/page", () => ({}));
vi.mock("@/app/(dashboard)/account-pool/page", () => ({}));
vi.mock("@/app/(dashboard)/usage/page", () => ({}));
vi.mock("@/app/(dashboard)/models-and-endpoints/preloadModels", () => ({ preloadModelsModules: async () => {} }));
vi.mock("@/features/account-pool/preloadModules", () => ({ preloadAccountPoolModules: async () => {} }));
vi.mock("@/components/view_logs", () => ({
  preloadFullLogModule: async () => {},
  preloadOtherLogModules: async () => {},
  preloadLogSettingsModule: async () => {},
  preloadOperationLogModule: async () => {},
}));
vi.mock("@/lib/dashboardBackground", () => ({ dashboardBackgroundTasks: () => [] }));
const router = {
  prefetch: vi.fn(),
  push: vi.fn(),
  replace: vi.fn(),
  back: vi.fn(),
  forward: vi.fn(),
  refresh: vi.fn(),
};
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ accessToken: "test", userID: "user", userRole: "Admin", token: "session" }),
}));
vi.mock("@/contexts/PluginModeContext", () => ({ usePluginMode: () => ({ mode: "ai-gateway" }) }));

describe("dashboard warmup wiring", () => {
  let client: QueryClient;
  let unregister: () => void;
  const urls: URL[] = [];
  const fetch = vi.fn<typeof globalThis.fetch>();
  beforeEach(() => {
    client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000, gcTime: Infinity } } });
    unregister = registerResponseCache(client);
    registerAuthTokenGetter(() => "test");
    urls.length = 0;
    fetch.mockReset().mockImplementation(async (input) => {
      const url = new URL(input instanceof Request ? input.url : String(input), "http://localhost");
      urls.push(url);
      if (url.pathname === "/v2/team/list") return Response.json({ teams: [], total_pages: 1 });
      if (url.pathname === "/logs/operations") return Response.json({ data: [], total: 200 });
      if (url.pathname === "/logs/full") return Response.json({ data: [], totals: { attempts: 150 } });
      if (url.pathname.includes("spend/logs")) return Response.json({ data: [], total_pages: 5, total: 250 });
      return Response.json({ data: [], results: [], metadata: {} });
    });
    vi.stubGlobal("fetch", fetch);
  });
  afterEach(() => {
    unregister();
    client.clear();
    vi.unstubAllGlobals();
  });

  it("prepares three log pages before revealing the dashboard, then loads every remaining page without clicks", async () => {
    const primaryRequests: string[] = [];
    const result = await runDashboardWarmup(
      { queryClient: client, router, accessToken: "test", userId: "user", userRole: "Admin", token: "session" },
      (state) => {
        if (state.stage === "background" && primaryRequests.length === 0) primaryRequests.push(...urls.map(String));
      },
      new AbortController().signal,
    );
    expect(result.primary.failed).toBe(0);
    const operationOffsets = (list: string[]) =>
      list
        .map((url) => new URL(url))
        .filter((url) => url.pathname === "/logs/operations")
        .map((url) => url.searchParams.get("offset"));
    expect(operationOffsets(primaryRequests)).toEqual(["0", "50", "100"]);
    expect(operationOffsets(urls.map(String))).toEqual(["0", "50", "100", "150"]);
    expect(urls.filter((url) => url.pathname === "/logs/full").map((url) => url.searchParams.get("offset"))).toEqual([
      "0",
      "50",
      "100",
    ]);
    const requestPages = urls
      .filter((url) => url.pathname.includes("spend/logs"))
      .map((url) => url.searchParams.get("page"));
    expect(requestPages).toEqual(["1", "2", "3", "4", "5"]);
    expect(result.background.failed).toBe(0);
    const count = fetch.mock.calls.length;
    await client.fetchQuery(
      requestLogsQueryOptions({
        accessToken: "test",
        token: "session",
        userRole: "Admin",
        userID: "user",
        columnFilters: [],
        activeTab: "request logs",
        isLiveTail: false,
        excludeInternalHealthChecks: false,
        ...initialLogsRange(client),
        pagination: { pageIndex: 1, pageSize: 50 },
        isCustomDate: false,
        sorting: DEFAULT_LOGS_SORTING,
      }),
    );
    expect(fetch).toHaveBeenCalledTimes(count);
  });

  it("keeps the dashboard hidden and shows retry instead of 100 percent on a primary failure", async () => {
    fetch.mockRejectedValue(new Error("offline"));
    render(
      <QueryClientProvider client={client}>
        <DashboardWarmup>
          <p>Dashboard content</p>
        </DashboardWarmup>
      </QueryClientProvider>,
    );
    expect(screen.queryByText("Dashboard content")).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "重试加载" })).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "0");
    expect(screen.queryByText("Dashboard content")).not.toBeInTheDocument();
  });

  it("continues loading other log pages when the full-log service is unavailable", async () => {
    const send = fetch.getMockImplementation()!;
    fetch.mockImplementation((input, init) => {
      const url = new URL(input instanceof Request ? input.url : String(input), "http://localhost");
      if (url.pathname === "/logs/full") return Promise.resolve(Response.json({ error: "offline" }, { status: 503 }));
      return send(input, init);
    });
    const result = await runDashboardWarmup(
      { queryClient: client, router, accessToken: "test", userId: "user", userRole: "Admin", token: "session" },
      () => {},
      new AbortController().signal,
    );
    expect(result.primary.failed).toBe(0);
    expect(result.background.failed).toBe(1);
    expect(
      urls.filter((url) => url.pathname === "/logs/operations").map((url) => url.searchParams.get("offset")),
    ).toContain("150");
  });

  it("refreshes primary and secondary data separately without clearing cached data", async () => {
    const main = vi.fn().mockResolvedValue("updated main");
    const secondary = vi.fn().mockResolvedValue("updated secondary");
    client.setQueryDefaults(["main"], { meta: { dashboardWarmup: "primary" } });
    client.setQueryDefaults(["secondary"], { meta: { dashboardWarmup: "background" } });
    await client.fetchQuery({ queryKey: ["main"], queryFn: main });
    await client.fetchQuery({ queryKey: ["secondary"], queryFn: secondary });
    main.mockClear();
    secondary.mockClear();
    await refreshWarmupQueries(client, true, new AbortController().signal);
    expect(main).toHaveBeenCalledOnce();
    expect(secondary).not.toHaveBeenCalled();
    await refreshWarmupQueries(client, false, new AbortController().signal);
    expect(secondary).toHaveBeenCalledOnce();
    expect(client.getQueryData(["main"])).toBe("updated main");
  });
});
