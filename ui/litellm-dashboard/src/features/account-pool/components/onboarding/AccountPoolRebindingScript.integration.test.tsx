/** 验证换绑脚本从网页发布时要求脚本发布密钥，新增辅助文件不拒收。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { AccountPoolLoginScriptSettings } from "./AccountPoolLoginScriptSettings";
import { getEmailBindingScript, updateEmailBindingScript } from "../../api/AccountPoolEmailRebindingApi";
vi.mock("../../api/AccountPoolEmailRebindingApi", () => ({
  getEmailBindingScript: vi.fn(),
  updateEmailBindingScript: vi.fn(),
}));
it("publishes rebind.mjs and extra directory data with a required publisher-key field", async () => {
  vi.mocked(getEmailBindingScript).mockResolvedValue({ upload_enabled: true, invalid: false, active: null });
  vi.mocked(updateEmailBindingScript).mockResolvedValue({
    upload_enabled: true,
    invalid: false,
    active: { entrypoint: "rebind.mjs", file_count: 2, sha256: "a".repeat(64) },
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AccountPoolLoginScriptSettings accessToken="fixture-admin" kind="rebinding" />
    </QueryClientProvider>,
  );
  await screen.findByText(/选 rebind.mjs/);
  const file = (name: string, source: string) =>
    Object.defineProperties(new File([source], name), {
      webkitRelativePath: { value: "folder/" + name },
      arrayBuffer: { value: async () => new TextEncoder().encode(source).buffer },
    });
  fireEvent.change(screen.getByLabelText("上传目录"), {
    target: { files: [file("rebind.mjs", "x"), file("added.json", "{}")] },
  });
  const publisherKey = "p".repeat(32);
  fireEvent.change(screen.getByLabelText("脚本发布密钥"), { target: { value: publisherKey } });
  fireEvent.click(screen.getByRole("button", { name: "发布脚本" }));
  await screen.findByText("已发布，下次换绑使用新脚本");
  expect(updateEmailBindingScript).toHaveBeenCalledWith("fixture-admin", publisherKey, {
    entrypoint: "rebind.mjs",
    files: [
      { path: "rebind.mjs", data: "eA==" },
      { path: "added.json", data: "e30=" },
    ],
  });
});
