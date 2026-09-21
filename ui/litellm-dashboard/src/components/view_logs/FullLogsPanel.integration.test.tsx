/** 本文件验证完整日志按需加载、会话查看及独立清理交互。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Activity } from "react";
import i18n from "@/i18n";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FullLogsPanel } from "./FullLogsPanel";
import { clearFullLogs, fullLogStorage, getFullLog, listFullLogs } from "./fullLogsApi";
vi.mock("./fullLogsApi", () => ({
  clearFullLogs: vi.fn(),
  fullLogStorage: vi.fn(),
  getFullLog: vi.fn(),
  listFullLogs: vi.fn(),
}));

const log = {
  event_id: "event-one",
  request_id: "request-one",
  card_id: "card-one",
  account_id: "account-one",
  key_id: "key-one",
  session_id: "session-one",
  started_at: "2026-09-16T12:00:00Z",
  finished_at: "2026-09-16T12:00:01Z",
  model: "model-a",
  requested_model: "alias",
  attempt: 1,
  result: {
    lease_id: "event-one",
    http_status: 499,
    model_cooldown_seconds: 0,
    retry_after_seconds: 0,
    cost_source: "unknown",
    full_log_state: "stored",
    method: "POST",
    retryable: false,
    switched_account: false,
    stage: "response",
    spend_sync_state: "unavailable",
    endpoint: "/v1/responses",
    message: "disconnected",
    input_tokens: 10,
    output_tokens: 4,
    cache_read_input_tokens: 8,
    cost_usd: null,
  },
  transport: "sse",
  incomplete: true,
  skip_failed: false,
  truncated: false,
  request: { input: "private question" },
  response: 'data: {"type":"response.output_text.delta","delta":"partial answer"}\n\n',
} as const;

describe("FullLogsPanel", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("zh-CN");
    vi.resetAllMocks();
    vi.mocked(listFullLogs).mockResolvedValue({
      items: [log],
      has_more: false,
      totals: {
        requests: 1,
        attempts: 1,
        input_tokens: 10,
        output_tokens: 4,
        cache_read_input_tokens: 8,
        cache_creation_input_tokens: 0,
        cost_usd: null,
        unknown_cost_attempts: 1,
      },
    });
    vi.mocked(fullLogStorage).mockResolvedValue({
      backend: "sqlite-gzip",
      location: "/full/conversations.sqlite3",
      row_count: 1,
      allocated_bytes: 4096,
    });
    vi.mocked(getFullLog).mockResolvedValue(log);
    vi.mocked(clearFullLogs).mockResolvedValue({ deleted: 1 });
  });
  afterEach(() => vi.useRealTimers());

  it("polls summaries, pauses while hidden and preserves an unfinished filter on return", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const panel = (visible: boolean) => (
      <QueryClientProvider client={client}>
        <Activity mode={visible ? "visible" : "hidden"}>
          <FullLogsPanel accessToken="admin" environments={[]} />
        </Activity>
      </QueryClientProvider>
    );
    const { rerender, unmount } = render(panel(true));
    await screen.findByRole("button", { name: "查看会话" });
    fireEvent.change(screen.getByRole("textbox", { name: "会话 ID" }), { target: { value: "unfinished" } });
    vi.useFakeTimers();
    vi.mocked(listFullLogs).mockClear();
    rerender(panel(false));
    rerender(panel(true));
    await act(() => vi.advanceTimersByTimeAsync(0));
    vi.mocked(listFullLogs).mockClear();
    await act(() => vi.advanceTimersByTimeAsync(15000));
    expect(listFullLogs).toHaveBeenCalledOnce();
    expect(getFullLog).not.toHaveBeenCalled();
    rerender(panel(false));
    await act(() => vi.advanceTimersByTimeAsync(30000));
    expect(listFullLogs).toHaveBeenCalledOnce();
    rerender(panel(true));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByRole("textbox", { name: "会话 ID" })).toHaveValue("unfinished");
    const count = vi.mocked(listFullLogs).mock.calls.length;
    fireEvent.click(screen.getByRole("switch", { name: "实时跟踪" }));
    await act(() => vi.advanceTimersByTimeAsync(30000));
    expect(listFullLogs).toHaveBeenCalledTimes(count);
    unmount();
    client.clear();
  });
  const mount = () =>
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <FullLogsPanel accessToken="admin" environments={[]} />
      </QueryClientProvider>,
    );
  it("loads bodies only on demand and marks interrupted output and unknown cost", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByText("/full/conversations.sqlite3");
    expect(getFullLog).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "查看完整日志" }));
    expect(await screen.findByText("private question")).toBeInTheDocument();
    expect(screen.getByText("partial answer")).toBeInTheDocument();
    expect(screen.getByText(/回复未完成/)).toBeInTheDocument();
    expect(screen.getByText("计价未知")).toBeInTheDocument();
  });
  it("filters the conversation and cleans only the selected complete-log range", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByRole("button", { name: "查看会话" }));
    expect(listFullLogs).toHaveBeenLastCalledWith(
      "admin",
      expect.objectContaining({ session_id: "session-one", offset: 0 }),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await user.selectOptions(screen.getByRole("combobox", { name: "完整日志清理范围" }), "7");
    await user.click(screen.getByRole("button", { name: "清理" }));
    expect(clearFullLogs).toHaveBeenCalledWith("admin", 7);
  });

  it("jumps pages and changes page size in 完整 logs requests", async () => {
    const user = userEvent.setup();
    vi.mocked(listFullLogs).mockResolvedValue({
      items: [log],
      has_more: true,
      totals: {
        attempts: 205,
        requests: 205,
        input_tokens: 0,
        output_tokens: 0,
        cache_read_input_tokens: 0,
        cache_creation_input_tokens: 0,
        unknown_cost_attempts: 205,
      },
    });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <FullLogsPanel accessToken="admin" environments={[]} />
      </QueryClientProvider>,
    );
    const jump = await screen.findByRole("spinbutton", { name: "跳转页码" });
    await waitFor(() => expect(jump).toBeEnabled());
    fireEvent.change(jump, { target: { value: "3" } });
    await user.click(screen.getByRole("button", { name: "跳转" }));
    await waitFor(() =>
      expect(vi.mocked(listFullLogs)).toHaveBeenLastCalledWith("admin", expect.objectContaining({ offset: 100 })),
    );
    expect(screen.getByText("查看历史记录或详情时暂停自动刷新")).toBeInTheDocument();
    await user.click(screen.getByTestId("pagination-page-size"));
    await user.click(screen.getByRole("option", { name: "100" }));
    await waitFor(() =>
      expect(vi.mocked(listFullLogs)).toHaveBeenLastCalledWith(
        "admin",
        expect.objectContaining({ offset: 0, limit: 100 }),
      ),
    );
  });
});
