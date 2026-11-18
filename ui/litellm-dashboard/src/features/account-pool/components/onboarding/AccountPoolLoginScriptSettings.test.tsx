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

it("uploads an arbitrary directory entry and retains data files", async () => {
  vi.mocked(getAutomationLoginScript).mockResolvedValue({ upload_enabled: true, invalid: false, active: null });
  vi.mocked(updateAutomationLoginScript).mockResolvedValue({
    upload_enabled: true, invalid: false, active: { entrypoint: "scripts/run.mjs", sha256: "a".repeat(64), file_count: 2 },
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><AccountPoolLoginScriptSettings accessToken="fake-admin" /></QueryClientProvider>);
  const upload = screen.getByLabelText("上传目录");
  await waitFor(() => expect(upload).toBeEnabled());
  const file = (name: string, path: string, data: number[]) => Object.defineProperties(new File(["x"], name), {
    webkitRelativePath: { value: "bundle/" + path },
    arrayBuffer: { value: async () => new Uint8Array(data).buffer },
  });
  fireEvent.change(upload, { target: { files: [file("run.mjs", "scripts/run.mjs", [120]), file("data.json", "data.json", [123, 125])] } });
  const key = "fixture-publisher-key-".repeat(2);
  const input = screen.getByLabelText("脚本发布密钥");
  fireEvent.change(input, { target: { value: key } });
  fireEvent.click(screen.getByRole("button", { name: "发布脚本" }));
  await screen.findByText("已发布，下次启动自动授权时使用");
  expect(updateAutomationLoginScript).toHaveBeenCalledWith("fake-admin", key, {
    entrypoint: "scripts/run.mjs", files: [{ path: "scripts/run.mjs", data: "eA==" }, { path: "data.json", data: "e30=" }],
  });
  expect(input).toHaveValue("");
});
