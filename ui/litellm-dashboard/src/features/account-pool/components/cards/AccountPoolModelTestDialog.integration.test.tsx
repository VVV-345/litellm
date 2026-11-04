/** 验证卡片连接测试表单到真实请求客户端的参数和结果展示。 */
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { apiClient } from "@/components/networking";
import { AccountPoolModelTestDialog } from "./AccountPoolModelTestDialog";

vi.mock("@/components/networking", () => ({ apiClient: { post: vi.fn() } }));

beforeEach(async () => {
  await i18n.changeLanguage("zh-CN");
  vi.clearAllMocks();
});
describe("card connection test", () => {
  it("sends selected card/model and shows complete result", async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      success: true,
      elapsed_ms: 75,
      message: "Model returned a complete response",
      error_code: null,
    });
    render(
      <AccountPoolModelTestDialog
        accessToken="admin"
        environment={{ id: "card-a", name: "Card A", enabled_models: ["model-a"] }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText(/会消耗少量额度/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch", { name: "流式响应" }));
    fireEvent.click(screen.getByRole("button", { name: "开始测试" }));
    expect(await screen.findByRole("status")).toHaveTextContent("测试成功");
    expect(screen.getByRole("status")).toHaveTextContent("75 ms");
    expect(apiClient.post).toHaveBeenCalledExactlyOnceWith(
      "/account_pool/environments/card-a/test",
      expect.objectContaining({ accessToken: "admin", body: { model: "model-a", mode: "chat", stream: true } }),
    );
  });
  it("shows failed model completion rather than success on HTTP 200", async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      success: false,
      elapsed_ms: 80,
      message: "incomplete",
      error_code: "incomplete_response",
    });
    render(
      <AccountPoolModelTestDialog
        accessToken="admin"
        environment={{ id: "card-a", name: "Card A", enabled_models: ["model-a"] }}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "开始测试" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("incomplete_response");
    expect(screen.queryByText("测试成功")).not.toBeInTheDocument();
  });
});


it("does not offer unverified access-only models or submit a stale selection", () => {
  const environment = {id: "card-a", name: "Card A", enabled_models: ["unproven", "proven"],
    access_token_fallback: {enabled: true, active: true, failure_source: "refresh", reason: "temporary_access", expires_at: "2090-01-01T00:00:00Z", verified_models: ["proven"]}};
  const {rerender} = render(<AccountPoolModelTestDialog accessToken="admin" environment={environment} onClose={vi.fn()} />);
  expect(screen.queryByText("unproven")).not.toBeInTheDocument();
  expect(screen.getByText("proven")).toBeVisible();
  rerender(<AccountPoolModelTestDialog accessToken="admin" environment={{...environment, access_token_fallback: {...environment.access_token_fallback, active: false}}} onClose={vi.fn()} />);
  expect(screen.getByRole("button", {name: "开始测试"})).toBeDisabled();
  expect(apiClient.post).not.toHaveBeenCalled();
});
