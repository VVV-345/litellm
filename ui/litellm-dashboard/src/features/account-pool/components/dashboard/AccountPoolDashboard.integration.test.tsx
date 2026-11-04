/** 验证仪表盘刷新间隔沿用认证 API，并只在保存成功后更新共享状态。 */
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { accountPoolQuotaRefreshStatusOptions } from "../../hooks/accountPoolOptions";
import { AccountPoolDashboard } from "./AccountPoolDashboard";

afterEach(() => vi.unstubAllGlobals());

it("saves a selected interval with authentication and retains the saved value after a rejected update", async () => {
  let minutes = 5;
  let fail = false;
  const writes: { path: string; authorization: string | null; body: unknown }[] = [];
  const status = () => ({
    interval_minutes: minutes,
    running: false,
    last_started_at: null,
    last_completed_at: null,
    next_refresh_at: null,
    last_failed_count: 0,
  });
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input, init) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        writes.push({ path, authorization: new Headers(init.headers).get("Authorization"), body });
        if (fail) return new Response(JSON.stringify({ detail: "fixture save rejected" }), { status: 409 });
        minutes = body.interval_minutes;
      }
      return new Response(JSON.stringify(status()), { headers: { "Content-Type": "application/json" } });
    }),
  );
  function Dashboard() {
    const query = useQuery(accountPoolQuotaRefreshStatusOptions("fixture-key"));
    return (
      <AccountPoolDashboard
        accessToken="fixture-key"
        environments={[]}
        statsByCard={new Map()}
        statsLoading={false}
        quotaRefreshStatus={query.data ?? null}
        onRefreshQuotas={() => undefined}
        refreshingQuotas={false}
        renderCard={() => null}
      />
    );
  }
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Dashboard />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  const selector = await screen.findByRole("combobox", { name: "自动刷新档位" });
  await waitFor(() => expect(selector).toBeEnabled());
  await user.click(selector);
  await user.click(await screen.findByRole("option", { name: "每 60 分钟" }));
  await waitFor(() => expect(selector).toHaveTextContent("每 60 分钟"));
  expect(writes[0]).toEqual({
    path: "/account_pool/quotas/refresh/interval",
    authorization: "Bearer fixture-key",
    body: { interval_minutes: 60 },
  });
  fail = true;
  await user.click(selector);
  await user.click(await screen.findByRole("option", { name: "每 15 分钟" }));
  await waitFor(() => expect(writes).toHaveLength(2));
  await waitFor(() => expect(selector).toBeEnabled());
  expect(selector).toHaveTextContent("每 60 分钟");
});
