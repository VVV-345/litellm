/** 验证两条上号流程独立、密码按需读取和等待授权的界面。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AccountPoolOnboardingPanel } from "./AccountPoolOnboardingPanel";

const { get, post, put } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock("@/components/networking", () => ({ apiClient: { get, post, put } }));
const item = {
  id: "one",
  job_id: "job",
  source: "oauth",
  supplier: "openai_codex",
  mailbox: "gmail",
  label: "account@example.com",
  state: "awaiting_authorization",
  message: "请完成登录后等待验证",
  card_id: "card",
  card_name: null,
  models: [],
  attempts: 1,
  created_at: "2026-09-19T12:00:00Z",
  updated_at: "2026-09-19T12:00:00Z",
};
const mount = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AccountPoolOnboardingPanel accessToken="test" />
    </QueryClientProvider>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  get.mockImplementation(async (path: string) => (path.endsWith("/items") ? [item] : []));
  post.mockResolvedValue({
    mailbox_password: "mail-only-secret",
    supplier_password: "provider-only-secret",
    proposed_password: null,
  });
});

describe("onboarding workflows", () => {
  it("keeps OAuth inventory out of the auth-file view and only reads secrets on demand", async () => {
    const user = userEvent.setup();
    mount();
    expect(await screen.findByText("暂无任务，先选择供应商并上传文件")).toBeInTheDocument();
    expect(screen.queryByText("account@example.com")).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "OAuth 上号" }));
    expect(await screen.findByText("account@example.com")).toBeInTheDocument();
    expect(screen.getByText("待完成授权")).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "查看 / 操作" }));
    expect(screen.queryByText("mail-only-secret")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看密码" }));
    expect(await screen.findByText("mail-only-secret")).toBeInTheDocument();
    expect(screen.getByText("provider-only-secret")).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith("/account_pool/onboarding/items/one/secrets", { accessToken: "test" });
    await user.click(screen.getByRole("button", { name: "隐藏密码" }));
    expect(screen.queryByText("mail-only-secret")).not.toBeInTheDocument();
  });
  it("updates supplier and mailbox selectors without creating or authorizing a card", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("tab", { name: "OAuth 上号" }));
    fireEvent.change(screen.getByLabelText("模型供应商"), { target: { value: "anthropic_claude" } });
    fireEvent.change(screen.getByLabelText("邮箱类型"), { target: { value: "mail" } });
    expect(screen.getByLabelText("模型供应商")).toHaveValue("anthropic_claude");
    expect(screen.getByLabelText("邮箱类型")).toHaveValue("mail");
    expect(post).not.toHaveBeenCalled();
  });
});
