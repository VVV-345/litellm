/** 验证两条上号流程独立、密码按需读取和等待授权的界面。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AccountPoolOnboardingPanel } from "./AccountPoolOnboardingPanel";

const { get, post, put } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock("@/components/networking", () => ({ serverRootPath: "", apiClient: { get, post, put } }));
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
  get.mockImplementation(async (path: string) => {
    if (path.endsWith("/items")) return [item];
    if (path.endsWith("/proxy-profiles")) return [{ id: "proxy-us", name: "Proxy US", protocol: "http" }];
    if (path.endsWith("/suppliers"))
      return [
        { supplier: "openai_codex", display_name: "Codex", authentication: "OAuth", oauth: true, auth_file: true },
        {
          supplier: "anthropic_claude",
          display_name: "Claude",
          authentication: "OAuth",
          oauth: true,
          auth_file: true,
        },
        { supplier: "gemini", display_name: "Gemini", authentication: "API Key", oauth: false, auth_file: false },
        {
          supplier: "vertex",
          display_name: "Vertex",
          authentication: "服务账号 JSON",
          oauth: false,
          auth_file: false,
        },
      ];
    return [];
  });
  post.mockImplementation(async (path: string) => {
    if (path.endsWith("/preview")) {
      return [{ index: 0, label: "account@example.com", status: "valid", message: "校验通过" }];
    }
    return {
      mailbox_password: "mail-only-secret",
      supplier_password: "provider-only-secret",
      proposed_password: null,
    };
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
    expect(await screen.findByRole("option", { name: /Gemini/ })).toBeDisabled();
    expect(screen.getByRole("option", { name: /Vertex/ })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("模型供应商"), { target: { value: "anthropic_claude" } });
    fireEvent.change(screen.getByLabelText("邮箱类型"), { target: { value: "mail" } });
    expect(screen.getByLabelText("模型供应商")).toHaveValue("anthropic_claude");
    expect(screen.getByLabelText("邮箱类型")).toHaveValue("mail");
    expect(post).not.toHaveBeenCalled();
  });

  it("requires and forwards a proxy profile for OAuth onboarding", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("tab", { name: "OAuth 上号" }));

    const fileInput = await screen.findByLabelText("账号 JSON 文件");
    expect(fileInput).toBeDisabled();
    await user.click(await screen.findByRole("combobox", { name: "OAuth 出站代理" }));
    await user.click(await screen.findByRole("option", { name: "Proxy US" }));
    expect(fileInput).toBeEnabled();

    const content = '[{"email":"account@example.com","mailbox_password":"mail-secret"}]';
    const file = new File([content], "accounts.json", { type: "application/json" });
    Object.defineProperty(file, "text", { value: async () => content });
    await user.upload(fileInput, file);

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        "/account_pool/onboarding/preview",
        expect.objectContaining({
          accessToken: "test",
          body: expect.objectContaining({ source: "oauth", proxy_profile_id: "proxy-us" }),
        }),
      ),
    );
  });
});
