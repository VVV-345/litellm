/** 验证刷新确认、重复提交保护和保存与推理结果分离。 */
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, expect, it, vi } from "vitest";
import { apiClient } from "@/components/networking";
import i18n from "@/i18n";
import { AccountPoolCredentialDialog } from "./AccountPoolCredentialDialog";
vi.mock("@/components/networking", () => ({ apiClient: { post: vi.fn() } }));
beforeEach(async () => {
  vi.resetAllMocks();
  await i18n.changeLanguage("zh-CN");
});
const open = (mode: "refresh" | "verify" = "refresh") =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AccountPoolCredentialDialog
        accessToken="fixture-admin"
        mode={mode}
        environment={{ id: "fixture", name: "测试卡", version: 7, enabled_models: ["gpt-6-astra"] }}
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  );
it("requires confirmation, sends current version and does not claim inference success", async () => {
  vi.mocked(apiClient.post).mockResolvedValue({ saved: true, reason: "saved", status: { last_result: "saved" } });
  open();
  expect(screen.getByRole("button", { name: "刷新凭据" })).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "刷新凭据" }));
  expect(await screen.findByText("凭据已保存，模型可用性尚未验证")).toBeInTheDocument();
  expect(apiClient.post).toHaveBeenCalledExactlyOnceWith(
    "/account_pool/environments/fixture/credentials/refresh",
    expect.objectContaining({ body: { version: 7, confirmed: true } }),
  );
  expect(screen.getByRole("button", { name: "刷新凭据" })).toBeDisabled();
});
it("does not claim temporary availability when inference passed but restoration failed", async () => {
  vi.mocked(apiClient.post).mockResolvedValue({ passed: true, restored: false, reason: "state_changed" });
  open("verify");
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "检查可用性" }));
  expect(await screen.findByText("推理通过，但尚未允许降级调用")).toBeInTheDocument();
  expect(apiClient.post).toHaveBeenCalledExactlyOnceWith(
    "/account_pool/environments/fixture/access-token-fallback/verify",
    expect.objectContaining({ body: { model: "gpt-6-astra", confirmed: true } }),
  );
});

it("submits only once during a pending request and redacts uncertain errors", async () => {
  let reject!: (reason: Error) => void;
  vi.mocked(apiClient.post).mockImplementation(
    () =>
      new Promise((_resolve, failure) => {
        reject = failure;
      }),
  );
  open();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.doubleClick(screen.getByRole("button", { name: "刷新凭据" }));
  fireEvent.click(screen.getByRole("button", { name: "刷新凭据" }));
  expect(screen.getByRole("checkbox")).toHaveAttribute("aria-disabled", "true");
  reject(new Error("synthetic-secret-must-not-render"));
  expect(await screen.findByRole("status")).not.toHaveTextContent("synthetic-secret-must-not-render");
  expect(apiClient.post).toHaveBeenCalledTimes(1);
});
