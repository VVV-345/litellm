/** 本文件验证号池日志页面展示可信成本覆盖率和显式路由原因。 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OperationLogsPanel } from "./OperationLogsPanel";
import type { AccountPoolEnvironment } from "@/features/account-pool/utils/AccountPoolTypes";

const listLogs = vi.fn();
const getLog = vi.fn();
const getStats = vi.fn();
const exportLogs = vi.fn();

vi.mock("./operationLogsApi", () => ({
  listOperationLogs: (...args: unknown[]) => listLogs(...args),
  getOperationLog: (...args: unknown[]) => getLog(...args),
  getOperationStats: (...args: unknown[]) => getStats(...args),
  exportOperationLogs: (...args: unknown[]) => exportLogs(...args),
  getOperationLogStorage: async () => ({ row_count: 0, allocated_bytes: 0 }),
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

describe("OperationLogsPanel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    listLogs.mockResolvedValue({ items: [log], has_more: false, total: 1 });
    getLog.mockResolvedValue({ event: log, attempts: [log], has_more: false });
    getStats.mockResolvedValue(stats);
  });

  it("shows per-request model, tokens and cache usage without treating missing usage as zero", async () => {
    listLogs.mockResolvedValue({
      items: [
        { ...log, cache_read_input_tokens: 8, cache_creation_input_tokens: 0, cache_rate: 0.8 },
        { ...log, event_id: "unknown", message: "No usage reported", input_tokens: null, output_tokens: null },
      ],
      has_more: false,
      total: 2,
    });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <OperationLogsPanel accessToken="token" environments={[]} />
      </QueryClientProvider>,
    );
    const row = await screen.findByRole("row", { name: /Request completed/ });
    expect(within(row).getByText(log.model)).toBeInTheDocument();
    expect(within(row).getByRole("cell", { name: "10" })).toBeInTheDocument();
    expect(within(row).getByRole("cell", { name: "5" })).toBeInTheDocument();
    expect(within(row).getByRole("cell", { name: "8" })).toBeInTheDocument();
    expect(within(row).getByRole("cell", { name: "$0.00042" })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "缓存写入" })).not.toBeInTheDocument();
    expect(within(row).getByRole("cell", { name: "80.0%" })).toBeInTheDocument();
    expect(within(row).getByText("50 ms")).toBeInTheDocument();
    const unknown = screen.getByRole("row", { name: /No usage reported/ });
    expect(within(unknown).getAllByRole("cell", { name: "暂无数据" })).toHaveLength(4);
  });

  it("shows reported cost coverage and the selected route reason", async () => {
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <OperationLogsPanel accessToken="token" environments={[]} />
      </QueryClientProvider>,
    );

    expect(await screen.findAllByText("$0.00042")).toHaveLength(2);
    expect(screen.getByText(/1.*3/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Request completed" }));
    expect(await screen.findAllByText(/卡片优先账号|Preferred card account/i)).not.toHaveLength(0);
  });

  it("keeps the page account filter across query, export and filter reset", async () => {
    const user = userEvent.setup();
    exportLogs.mockRejectedValue(new Error("test export"));
    const card = { id: log.card_id, name: "测试账号" } as AccountPoolEnvironment;
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <OperationLogsPanel accessToken="token" environments={[card]} initialCardId={card.id} />
      </QueryClientProvider>,
    );
    await screen.findByRole("row", { name: /Request completed/ });
    await user.click(screen.getByRole("combobox", { name: "请求结果" }));
    await user.click(screen.getByRole("option", { name: "失败" }));
    fireEvent.change(screen.getByLabelText("HTTP 状态码"), { target: { value: "429" } });
    fireEvent.change(screen.getByLabelText("会话 ID"), { target: { value: "pool-session" } });
    await user.click(screen.getByRole("button", { name: "查询" }));
    const expected = { card_id: card.id, final_status: "failed", http_status: 429, session_id: "pool-session" };
    await waitFor(() => expect(listLogs).toHaveBeenLastCalledWith("token", expect.objectContaining(expected)));
    expect(getStats).toHaveBeenLastCalledWith("token", expect.objectContaining(expected));
    await user.click(screen.getByRole("button", { name: "导出日志" }));
    expect(exportLogs).toHaveBeenLastCalledWith("token", expect.objectContaining(expected));
    await user.click(screen.getByRole("button", { name: "重置筛选" }));
    expect(screen.getByLabelText("HTTP 状态码")).toHaveValue(null);
    expect(screen.getByLabelText("会话 ID")).toHaveValue("");
    await waitFor(() =>
      expect(listLogs).toHaveBeenLastCalledWith("token", expect.objectContaining({ card_id: card.id, offset: 0 })),
    );
  });

  it("jumps pages and changes page size in 运行 logs requests", async () => {
    const user = userEvent.setup();
    listLogs.mockResolvedValue({ items: [log], has_more: true, total: 205, totals: { attempts: 205 } });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <OperationLogsPanel accessToken="token" environments={[]} />
      </QueryClientProvider>,
    );
    const jump = await screen.findByRole("spinbutton", { name: "跳转页码" });
    await waitFor(() => expect(jump).toBeEnabled());
    fireEvent.change(jump, { target: { value: "3" } });
    await user.click(screen.getByRole("button", { name: "跳转" }));
    await waitFor(() => expect(listLogs).toHaveBeenLastCalledWith("token", expect.objectContaining({ offset: 100 })));
    expect(screen.getByText("查看历史记录或详情时暂停自动刷新")).toBeInTheDocument();
    await user.click(screen.getByTestId("pagination-page-size"));
    await user.click(screen.getByRole("option", { name: "100" }));
    await waitFor(() =>
      expect(listLogs).toHaveBeenLastCalledWith("token", expect.objectContaining({ offset: 0, limit: 100 })),
    );
  });
});
