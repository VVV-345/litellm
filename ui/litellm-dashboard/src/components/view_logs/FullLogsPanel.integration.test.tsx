/** 本文件验证完整日志按需加载、会话查看及独立清理交互。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
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
  beforeEach(() => {
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
    expect(screen.getByText(/估算成本：价格或用量未知/)).toBeInTheDocument();
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
});
