/** 本文件验证号池日志页面展示可信成本覆盖率和显式路由原因。 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountPoolLogsPanel } from "./AccountPoolLogsPanel";

const listLogs = vi.fn();
const getLog = vi.fn();
const getStats = vi.fn();

vi.mock("./AccountPoolManagementApi", () => ({
  listAccountPoolLogs: (...args: unknown[]) => listLogs(...args),
  getAccountPoolLog: (...args: unknown[]) => getLog(...args),
  getAccountPoolStats: (...args: unknown[]) => getStats(...args),
}));

const log = {
  event_id: "00000000-0000-4000-8000-000000000001",
  occurred_at: "2026-09-10T00:00:00Z",
  finished_at: "2026-09-10T00:00:01Z",
  channel: "cliproxyapi",
  supplier: "openai_codex",
  card_id: "00000000-0000-4000-8000-000000000002",
  environment_id: "00000000-0000-4000-8000-000000000003",
  account_id: "00000000-0000-4000-8000-000000000003",
  request_id: "00000000-0000-4000-8000-000000000004",
  attempt: 1,
  operation: "model_request",
  stage: "response",
  model: "gpt-5",
  endpoint: "/v1/responses",
  method: "POST",
  severity: "info",
  http_status: 200,
  retryable: false,
  retry_count: 0,
  switched_account: false,
  message: "Request completed",
  duration_ms: 50,
  input_tokens: 10,
  output_tokens: 5,
  routing_reason: "preferred_account",
  cost_usd: 0.00042,
  final_status: "succeeded",
} as const;

const stats = {
  total_requests: 3,
  succeeded_requests: 3,
  failed_requests: 0,
  retried_requests: 0,
  input_tokens: 10,
  output_tokens: 5,
  known_cost_requests: 1,
  total_cost_usd: 0.00042,
  average_duration_ms: 50,
  recent_errors: [],
};

describe("AccountPoolLogsPanel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    listLogs.mockResolvedValue({ items: [log], has_more: false });
    getLog.mockResolvedValue({ event: log, attempts: [log], has_more: false });
    getStats.mockResolvedValue(stats);
  });

  it("shows per-request model, tokens and cache usage without treating missing usage as zero", async () => {
    listLogs.mockResolvedValue({
      items: [
        { ...log, cache_read_input_tokens: 8, cache_creation_input_tokens: 0 },
        { ...log, event_id: "unknown", message: "No usage reported", input_tokens: null, output_tokens: null },
      ],
      has_more: false,
    });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AccountPoolLogsPanel accessToken="token" environments={[]} />
      </QueryClientProvider>,
    );
    const row = await screen.findByRole("row", { name: /Request completed/ });
    expect(within(row).getByText(log.model)).toBeInTheDocument();
    expect(within(row).getByRole("cell", { name: "10" })).toBeInTheDocument();
    expect(within(row).getByRole("cell", { name: "5" })).toBeInTheDocument();
    expect(within(row).getByRole("cell", { name: "8" })).toBeInTheDocument();
    expect(within(row).getByRole("cell", { name: "0" })).toBeInTheDocument();
    expect(within(row).getByText("50 ms")).toBeInTheDocument();
    const unknown = screen.getByRole("row", { name: /No usage reported/ });
    expect(within(unknown).getAllByRole("cell", { name: "暂无数据" })).toHaveLength(4);
  });

  it("shows reported cost coverage and the selected route reason", async () => {
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AccountPoolLogsPanel accessToken="token" environments={[]} />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("$0.00042")).toBeInTheDocument();
    expect(screen.getByText(/1.*3/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Request completed" }));
    expect(await screen.findAllByText(/卡片优先账号|Preferred card account/i)).not.toHaveLength(0);
  });
});
