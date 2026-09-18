import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { apiClient } from "@/components/networking";
import { LogSettingsPanel } from "./LogSettingsPanel";

vi.mock("@/components/networking", () => ({ apiClient: { get: vi.fn(), put: vi.fn() } }));

it("saves complete-log options once through the unified API with the current version", async () => {
  const values = {
    full_logging_enabled: false,
    full_log_skip_failed: false,
    daily_log_retention_days: 30,
    full_log_retention_days: 30,
    file_logging_enabled: false,
    debug_logging_enabled: false,
    request_log_enabled: false,
    usage_statistics_enabled: false,
    logs_max_total_size_mb: 0,
    error_logs_max_files: 10,
  };
  vi.mocked(apiClient.get).mockResolvedValue({ version: 7, values });
  vi.mocked(apiClient.put).mockResolvedValue({ version: 8, values });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <LogSettingsPanel accessToken="test" />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.click(await screen.findByRole("switch", { name: "记录完整日志（输入、提示词、回复和工具调用）" }));
  await user.click(screen.getByRole("switch", { name: "失败请求不保存完整日志" }));
  fireEvent.change(screen.getByRole("spinbutton", { name: "完整日志保留天数" }), { target: { value: "14" } });
  expect(apiClient.put).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "保存日志设置" }));
  await waitFor(() =>
    expect(apiClient.put).toHaveBeenCalledWith("/logs/settings", {
      accessToken: "test",
      body: {
        version: 7,
        values: { ...values, full_logging_enabled: true, full_log_skip_failed: true, full_log_retention_days: 14 },
      },
    }),
  );
});
