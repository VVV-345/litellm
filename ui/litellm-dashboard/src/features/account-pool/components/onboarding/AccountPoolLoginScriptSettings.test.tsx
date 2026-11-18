/** 验证目录发布能选入口并保留新增资源。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi, expect, it } from "vitest";
import { AccountPoolLoginScriptSettings } from "./AccountPoolLoginScriptSettings";
import { getAutomationLoginScript, updateAutomationLoginScript } from "../../api/AccountPoolAutomationApi";

vi.mock("../../api/AccountPoolAutomationApi", () => ({
  getAutomationLoginScript: vi.fn(),
  updateAutomationLoginScript: vi.fn(),
}));

it.each(["file", "directory"])("publishes a %s selection without requiring a wrapper directory", async (kind) => {
  const single = kind === "file";
  const entrypoint = single ? "login.mjs" : "scripts/run.mjs";
  vi.mocked(getAutomationLoginScript).mockResolvedValue({ upload_enabled: true, invalid: false, active: null });
  vi.mocked(updateAutomationLoginScript).mockResolvedValue({
    upload_enabled: true, invalid: false, active: { entrypoint, sha256: "a".repeat(64), file_count: single ? 1 : 2 },
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><AccountPoolLoginScriptSettings accessToken="fake-admin" /></QueryClientProvider>);
  const upload = screen.getByLabelText(single ? "上传单文件" : "上传目录");
  await waitFor(() => expect(upload).toBeEnabled());
  const file = (name: string, path: string, data: number[]) => Object.defineProperties(new File(["x"], name), {
    webkitRelativePath: { value: single ? "" : "bundle/" + path },
    arrayBuffer: { value: async () => new Uint8Array(data).buffer },
  });
  const selected = single ? [file("login.mjs", "", [120])]
    : [file("run.mjs", entrypoint, [120]), file("data.json", "data.json", [123, 125])];
  fireEvent.change(upload, { target: { files: selected } });
  expect(screen.queryByLabelText(/^入口文件/)).not.toBeInTheDocument();
  const key = "fixture-publisher-key-".repeat(2);
  const input = screen.getByLabelText("脚本发布密钥");
  fireEvent.change(input, { target: { value: key } });
  fireEvent.click(screen.getByRole("button", { name: "发布脚本" }));
  await screen.findByText("已发布，下次启动自动授权时使用");
  expect(updateAutomationLoginScript).toHaveBeenCalledWith("fake-admin", key, {
    entrypoint, files: [{ path: entrypoint, data: "eA==" }, ...(single ? [] : [{ path: "data.json", data: "e30=" }])],
  });
  expect(input).toHaveValue("");
});

it("shows the upload requirements without unusable controls when publishing is disabled", async () => {
  vi.mocked(getAutomationLoginScript).mockResolvedValue({ upload_enabled: false, invalid: false, active: null });
  render(<QueryClientProvider client={new QueryClient()}><AccountPoolLoginScriptSettings accessToken="fake-admin" /></QueryClientProvider>);
  await screen.findByText(/上传未启用/);
  expect(screen.getByText(/独立脚本：选 login.mjs/)).toBeVisible();
  expect(screen.queryByLabelText("脚本发布密钥")).not.toBeInTheDocument();
});
