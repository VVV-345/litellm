import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DashboardWarmup, { runDashboardWarmup } from "./DashboardWarmup";
import { useDashboardReadiness } from "./DashboardReadinessContext";
import { registerResponseCache } from "@/lib/http/responseCache";
import { registerAuthTokenGetter } from "@/lib/http/runtime";
import { initialLogsRange, requestLogsQueryOptions, DEFAULT_LOGS_SORTING } from "./view_logs/log_filter_logic";

const modules = vi.hoisted(() => ({ loaded: new Set<number>(), wait: async () => {}, failModules: false }));
const auth = vi.hoisted(() => ({ accessToken: "test", userID: "user", userRole: "Admin", token: "session" }));
const initialAuth = { ...auth };

vi.mock("@/app/(dashboard)/api-keys/page", () => ({}));
vi.mock("@/app/(dashboard)/models-and-endpoints/page", () => ({}));
vi.mock("@/app/(dashboard)/account-pool/page", () => ({}));
vi.mock("@/app/(dashboard)/usage/page", () => ({}));
vi.mock("@/app/(dashboard)/models-and-endpoints/preloadModels", () => ({ preloadModelsModules: async () => {} }));
vi.mock("@/features/account-pool/preloadModules", () => {
  const loaders = Array.from({ length: 12 }, (_, index) => async () => {
    await modules.wait();
    modules.loaded.add(index);
  });
  return {
    ACCOUNT_POOL_MODULE_LOADERS: loaders,
    preloadAccountPoolModules: () => {
      if (modules.failModules) return Promise.reject(new Error("ChunkLoadError: Loading chunk 9 failed"));
      return Promise.all(loaders.map((load) => load()));
    },
  };
});
vi.mock("@/components/view_logs", () => ({
  preloadFullLogModule: async () => {},
  preloadOtherLogModules: async () => {},
  preloadLogSettingsModule: async () => {},
  preloadOperationLogModule: async () => {},
}));
vi.mock("@/lib/dashboardBackground", () => ({ dashboardBackgroundTasks: () => [] }));
const router = {
  bfcacheId: "test",
  prefetch: vi.fn(),
  push: vi.fn(),
  replace: vi.fn(),
  back: vi.fn(),
  forward: vi.fn(),
  refresh: vi.fn(),
};
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => auth,
}));
vi.mock("@/contexts/PluginModeContext", () => ({ usePluginMode: () => ({ mode: "ai-gateway" }) }));

function ReadinessProbe() {
  return <p>{useDashboardReadiness() ? "warm" : "warming"}</p>;
}

describe("dashboard warmup wiring", () => {
  let client: QueryClient;
  let unregister: () => void;
  let controller: AbortController;
  const urls: URL[] = [];
  const fetch = vi.fn<typeof globalThis.fetch>();
  beforeEach(() => {
    Object.assign(auth, initialAuth);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000, gcTime: Infinity } } });
    unregister = registerResponseCache(client);
    registerAuthTokenGetter(() => "test");
    urls.length = 0;
    modules.loaded.clear();
    modules.wait = async () => {};
    modules.failModules = false;
    controller = new AbortController();
    fetch.mockReset().mockImplementation(async (input) => {
      const url = new URL(input instanceof Request ? input.url : String(input), "http://localhost");
      urls.push(url);
      if (urls.length > 60) controller.abort();
      if (url.pathname === "/v2/team/list") return Response.json({ teams: [], total_pages: 1 });
      if (url.pathname === "/logs/operations") return Response.json({ data: [], total: 50000 });
      if (url.pathname === "/logs/full/sessions")
        return Response.json({ data: [], total: 50000, totals: { attempts: 50000 } });
      if (url.pathname.includes("spend/logs")) return Response.json({ data: [], total_pages: 1000, total: 50000 });
      return Response.json({ data: [], results: [], metadata: {} });
    });
    vi.stubGlobal("fetch", fetch);
  });
  afterEach(() => {
    unregister();
    client.clear();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("does not restart pending warmup when module switches have unchanged contents", async () => {
    vi.useFakeTimers();
    router.prefetch.mockClear();
    const pending = Promise.withResolvers<void>();
    modules.wait = () => pending.promise;
    const page = (disabledPages: readonly string[]) => (
      <QueryClientProvider client={client}>
        <DashboardWarmup disabledPages={disabledPages}>
          <ReadinessProbe />
        </DashboardWarmup>
      </QueryClientProvider>
    );
    const { rerender, unmount } = render(page(["agents"]));
    await act(() => vi.advanceTimersByTimeAsync(1000));
    const initial = router.prefetch.mock.calls.length;
    expect(initial).toBe(5);
    rerender(page(["agents"]));
    await act(() => vi.advanceTimersByTimeAsync(1000));
    const repeated = router.prefetch.mock.calls.length - initial;
    process.stdout.write(`warmup-dispatch-count initial=${initial} unchanged-rerender=${repeated}\n`);
    expect(repeated).toBe(0);
    pending.resolve();
    await act(() => vi.runAllTimersAsync());
    expect(screen.getByText("warm")).toBeInTheDocument();
    expect(modules.loaded.size).toBe(12);
    unmount();
  });

  it("does not dispatch warmup again when switching rendered content after completion", async () => {
    vi.useFakeTimers();
    router.prefetch.mockClear();
    const page = (label: string) => (
      <QueryClientProvider client={client}>
        <DashboardWarmup disabledPages={["agents"]}>
          <p>{label}</p>
        </DashboardWarmup>
      </QueryClientProvider>
    );
    const { rerender, unmount } = render(page("Keys page"));
    await act(() => vi.runAllTimersAsync());
    const initial = router.prefetch.mock.calls.length;
    const requests = fetch.mock.calls.length;
    rerender(page("Logs page"));
    await act(() => vi.runAllTimersAsync());
    expect(screen.getByText("Logs page")).toBeInTheDocument();
    process.stdout.write(`warmup-navigation-count initial=${initial} page-switch=${router.prefetch.mock.calls.length - initial}\n`);
    expect(router.prefetch).toHaveBeenCalledTimes(initial);
    expect(fetch).toHaveBeenCalledTimes(requests);
    unmount();
  });

  it.each(["accessToken", "userID", "userRole", "token", "disabledPages"] as const)(
    "still restarts warmup when %s changes",
    async (field) => {
      vi.useFakeTimers();
      router.prefetch.mockClear();
      const pending = Promise.withResolvers<void>();
      modules.wait = () => pending.promise;
      const page = (disabledPages: readonly string[]) => (
        <QueryClientProvider client={client}>
          <DashboardWarmup disabledPages={disabledPages}>
            <ReadinessProbe />
          </DashboardWarmup>
        </QueryClientProvider>
      );
      const { rerender, unmount } = render(page(["agents"]));
      await act(() => vi.advanceTimersByTimeAsync(1000));
      const initial = router.prefetch.mock.calls.length;
      if (field !== "disabledPages") auth[field] = `${auth[field]}-changed`;
      rerender(page(field === "disabledPages" ? ["agents", "memory"] : ["agents"]));
      await act(() => vi.advanceTimersByTimeAsync(1000));
      expect(router.prefetch).toHaveBeenCalledTimes(initial + 5);
      unmount();
      pending.resolve();
      await act(() => vi.runAllTimersAsync());
    },
  );

  it("blocks the initial page while primary warmup starts without waiting for idle", async () => {
    vi.useFakeTimers();
    const idle = vi.fn(() => 1);
    vi.stubGlobal("requestIdleCallback", idle);
    vi.stubGlobal("cancelIdleCallback", vi.fn());
    const { unmount } = render(
      <QueryClientProvider client={client}>
        <DashboardWarmup>
          <input aria-label="Current form" defaultValue="draft" />
        </DashboardWarmup>
      </QueryClientProvider>,
    );
    expect(screen.queryByRole("textbox", { name: "Current form" })).not.toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "主要模块加载进度" })).toHaveAttribute("aria-valuenow", "0");
    await act(() => vi.advanceTimersByTimeAsync(100));
    expect(fetch).toHaveBeenCalled();
    unmount();
  });

  it("reveals the initial page only after every required account-pool module is loaded", async () => {
    vi.useFakeTimers();
    const pending = Promise.withResolvers<void>();
    modules.wait = () => pending.promise;
    const { unmount } = render(
      <QueryClientProvider client={client}>
        <DashboardWarmup>
          <ReadinessProbe />
        </DashboardWarmup>
      </QueryClientProvider>,
    );
    expect(screen.queryByText("warm")).not.toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(screen.queryByText("warm")).not.toBeInTheDocument();
    expect(modules.loaded.size).toBe(0);
    pending.resolve();
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(screen.getByText("warm")).toBeInTheDocument();
    expect(modules.loaded.size).toBe(12);
    unmount();
  });

  it("finishes all twelve account-pool modules in primary before discovering background work", async () => {
    vi.useFakeTimers();
    const loadedAtBackground: number[] = [];
    const context = {
      queryClient: client, router, accessToken: "test", userId: "user", userRole: "Admin", token: "session",
    };
    const finished = runDashboardWarmup(
      context,
      (state) => {
        if (state.stage === "background") loadedAtBackground.push(modules.loaded.size);
      },
      controller.signal,
    );
    await vi.runAllTimersAsync();
    expect((await finished).primary.failed).toBe(0);
    expect(loadedAtBackground).not.toHaveLength(0);
    expect(loadedAtBackground.every((count) => count === 12)).toBe(true);
  });

  it("does not preload protected account-pool modules or data for an internal viewer", async () => {
    vi.useFakeTimers();
    const context = {
      queryClient: client, router, accessToken: "test", userId: "user", userRole: "Internal Viewer", token: "session",
    };
    const finished = runDashboardWarmup(
      context,
      () => {},
      controller.signal,
    );
    await vi.runAllTimersAsync();
    expect((await finished).stage).toBe("complete");
    expect(modules.loaded.size).toBe(0);
    expect(urls.some((url) => url.pathname.startsWith("/account_pool/"))).toBe(false);
    expect(urls.some((url) => url.pathname === "/logs/full/sessions")).toBe(false);
  });

  it("preloads only the first log pages even with fifty thousand historical records", async () => {
    const primaryRequests: string[] = [];
    const result = await runDashboardWarmup(
      { queryClient: client, router, accessToken: "test", userId: "user", userRole: "Admin", token: "session" },
      (state) => {
        if (state.stage === "background" && primaryRequests.length === 0) primaryRequests.push(...urls.map(String));
      },
      controller.signal,
    );
    expect(result.primary.failed).toBe(0);
    const operationOffsets = (list: string[]) =>
      list
        .map((url) => new URL(url))
        .filter((url) => url.pathname === "/logs/operations")
        .map((url) => url.searchParams.get("offset"));
    expect(operationOffsets(primaryRequests)).toEqual(["0"]);
    expect(operationOffsets(urls.map(String))).toEqual(["0"]);
    expect(
      urls.filter((url) => url.pathname === "/logs/full/sessions").map((url) => url.searchParams.get("offset")),
    ).toEqual(["0"]);
    const requestPages = urls
      .filter((url) => url.pathname.includes("spend/logs"))
      .map((url) => url.searchParams.get("page"));
    expect(requestPages).toEqual(["1"]);
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
        pagination: { pageIndex: 0, pageSize: 50 },
        isCustomDate: false,
        sorting: DEFAULT_LOGS_SORTING,
      }),
    );
    expect(fetch).toHaveBeenCalledTimes(count);
  });

  it("reveals the page when only a data prefetch fails and still offers retry or a new login", async () => {
    fetch.mockRejectedValue(new Error("offline"));
    const clear = vi.spyOn(await import("@/utils/cookieUtils"), "clearTokenCookies").mockImplementation(() => {});
    render(
      <QueryClientProvider client={client}>
        <DashboardWarmup>
          <p>Dashboard content</p>
        </DashboardWarmup>
      </QueryClientProvider>,
    );
    expect(await screen.findByText("Dashboard content", {}, { timeout: 10_000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试加载" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新登录" }));
    expect(clear).toHaveBeenCalledOnce();
  });

  it("keeps the initial page blocked and offers retry or a new login when a required module fails", async () => {
    modules.failModules = true;
    render(
      <QueryClientProvider client={client}>
        <DashboardWarmup>
          <p>Dashboard content</p>
        </DashboardWarmup>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("button", { name: "重试加载" }, { timeout: 10_000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新登录" })).toBeInTheDocument();
    expect(screen.queryByText("Dashboard content")).not.toBeInTheDocument();
  });

  it("continues loading other log pages when the full-log service is unavailable", async () => {
    const send = fetch.getMockImplementation()!;
    fetch.mockImplementation((input, init) => {
      const url = new URL(input instanceof Request ? input.url : String(input), "http://localhost");
      if (url.pathname === "/logs/full/sessions")
        return Promise.resolve(Response.json({ error: "offline" }, { status: 503 }));
      return send(input, init);
    });
    const result = await runDashboardWarmup(
      { queryClient: client, router, accessToken: "test", userId: "user", userRole: "Admin", token: "session" },
      () => {},
      controller.signal,
    );
    expect(result.primary.failed).toBe(0);
    expect(result.background.failed).toBe(1);
    expect(
      urls.filter((url) => url.pathname === "/logs/operations").map((url) => url.searchParams.get("offset")),
    ).toEqual(["0"]);
  });

  it("does not periodically refetch inactive pages after warmup completes", async () => {
    vi.useFakeTimers();
    const { unmount } = render(
      <QueryClientProvider client={client}>
        <DashboardWarmup>
          <p>Dashboard content</p>
        </DashboardWarmup>
      </QueryClientProvider>,
    );
    await act(() => vi.runAllTimersAsync());
    expect(urls.some((url) => url.pathname === "/logs/full/sessions")).toBe(true);
    const count = fetch.mock.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(10 * 60_000));
    expect(fetch).toHaveBeenCalledTimes(count);
    unmount();
    vi.useRealTimers();
  });
});
