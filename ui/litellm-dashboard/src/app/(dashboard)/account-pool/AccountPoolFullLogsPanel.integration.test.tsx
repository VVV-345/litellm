/** 本文件验证完整日志按需加载、会话查看及独立清理交互。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AccountPoolFullLogsPanel } from "./AccountPoolFullLogsPanel";
import { clearFullLogs, fullLogStorage, getFullLog, listFullLogs } from "./AccountPoolFullLogsApi";
import { getAccountPoolSettings, updateAccountPoolSettings } from "./AccountPoolManagementApi";

vi.mock("./AccountPoolManagementApi", () => ({ getAccountPoolSettings: vi.fn(), updateAccountPoolSettings: vi.fn() }));

const settingsValues = {
  default_route: "auto",
  default_concurrency_limit: 1,
  default_model_discovery: true,
  default_proxy_profile_id: null,
  max_attempts: 1,
  request_timeout_seconds: 120,
  full_logging_enabled: true,
  full_log_skip_failed: false,
  daily_log_retention_days: 30,
  full_log_retention_days: 30,
  auth_refresh_interval_minutes: 15,
  quota_refresh_interval_minutes: 5,
  file_logging_enabled: false,
  debug_logging_enabled: false,
  websocket_enabled: false,
  request_log_enabled: false,
  websocket_auth_enabled: false,
  force_model_prefix: false,
  request_retry: 1,
  max_retry_credentials: 1,
  max_retry_interval: 0,
  usage_statistics_enabled: false,
  logs_max_total_size_mb: 0,
  error_logs_max_files: 10,
  quota_switch_project: false,
  quota_switch_preview_model: false,
  oauth_excluded_models: [],
  oauth_model_aliases: {},
  oauth_request_scoped_errors: {},
  payload: { default: [], "default-raw": [], override: [], "override-raw": [], filter: [] },
  plugins_enabled: false,
  streaming_enabled: true,
  common_profiles: [],
  access_profiles: [],
  network_profiles: [],
  quota_profiles: [],
  streaming_profiles: [],
  advanced_profiles: [],
  payload_profiles: [],
  streaming_rules: [],
} satisfies Awaited<ReturnType<typeof getAccountPoolSettings>>["values"];

vi.mock("./AccountPoolFullLogsApi", () => ({
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

describe("AccountPoolFullLogsPanel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getAccountPoolSettings).mockResolvedValue({ version: 7, values: settingsValues, requires_reload: false });
    vi.mocked(updateAccountPoolSettings).mockResolvedValue({
      version: 8,
      values: { ...settingsValues, full_log_skip_failed: true },
      requires_reload: false,
    });
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
        <AccountPoolFullLogsPanel accessToken="admin" environments={[]} />
      </QueryClientProvider>,
    );
  it("saves the failed-request switch explicitly without clearing existing logs", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByText("/full/conversations.sqlite3");
    await user.click(screen.getByRole("switch", { name: "失败请求不保存完整日志" }));
    expect(updateAccountPoolSettings).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(updateAccountPoolSettings).toHaveBeenCalledWith("admin", {
      version: 7,
      values: { ...settingsValues, full_log_skip_failed: true },
    });
    expect(clearFullLogs).not.toHaveBeenCalled();
  });
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
