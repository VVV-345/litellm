/** 验证两条上号流程独立、密码按需读取和等待授权的界面。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  it("accepts two-factor text in file import and navigates to OAuth tasks after submission", async () => {
    const user = userEvent.setup();
    post.mockImplementation(async (path: string) =>
      path.endsWith("/preview")
        ? [{ index: 0, label: "fixture@example.test", status: "valid", message: "已识别 2FA 登录资料" }]
        : { items: [{ ...item, state: "queued" }], preview: [] },
    );
    mount();
    const input = await screen.findByLabelText("认证 JSON 文件（可多选）");
    await waitFor(() => expect(input).toBeEnabled());
    await user.click(screen.getByRole("combobox", { name: "认证文件出站代理" }));
    await user.click(await screen.findByRole("option", { name: "Proxy US" }));
    const content = "fixture@example.test----password----JBSWY3DPEHPK3PXP";
    const file = new File([content], "accounts.txt", { type: "text/plain" });
    Object.defineProperty(file, "text", { value: async () => content });
    await user.upload(input, file);
    await user.click(await screen.findByRole("button", { name: "导入有效项" }));
    expect(await screen.findByRole("tab", { name: "OAuth 上号", selected: true })).toBeVisible();
    expect(post).toHaveBeenCalledWith(
      "/account_pool/onboarding/imports",
      expect.objectContaining({
        body: expect.objectContaining({
          source: "auth_file",
          proxy_profile_id: "proxy-us",
          entries: [expect.objectContaining({ content })],
        }),
      }),
    );
  });
  it("allows optional node selection for JSON auth files and resets a stale preview when the node changes", async () => {
    const user = userEvent.setup();
    mount();
    const input = await screen.findByLabelText("认证 JSON 文件（可多选）");
    await waitFor(() => expect(input).toBeEnabled());
    await user.click(await screen.findByRole("combobox", { name: "认证文件出站代理" }));
    await user.click(await screen.findByRole("option", { name: "Proxy US" }));
    const content = '{"type":"codex","refresh_token":"fixture"}';
    const file = new File([content], "account.json", { type: "application/json" });
    Object.defineProperty(file, "text", { value: async () => content });
    await user.upload(input, file);
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        "/account_pool/onboarding/preview",
        expect.objectContaining({
          body: expect.objectContaining({ source: "auth_file", proxy_profile_id: "proxy-us" }),
        }),
      ),
    );
    expect(screen.getByRole("button", { name: "导入有效项" })).toBeVisible();
    await user.click(screen.getByRole("combobox", { name: "认证文件出站代理" }));
    await user.click(await screen.findByRole("option", { name: "沿用文件 / 默认配置" }));
    expect(screen.queryByRole("button", { name: "导入有效项" })).not.toBeInTheDocument();
  });

  it("requires confirmation and forwards the current source and timezone-aware cleanup cutoff", async () => {
    const user = userEvent.setup();
    post.mockResolvedValue({ cleared: 1 });
    mount();
    await user.click(screen.getByRole("button", { name: "清理已完成记录" }));
    expect(post).not.toHaveBeenCalled();
    const dialog = within(screen.getByRole("dialog"));
    await user.selectOptions(dialog.getByLabelText("清理范围"), "before");
    expect(dialog.getByRole("button", { name: "确认清理" })).toBeDisabled();
    fireEvent.change(dialog.getByLabelText("完成时间早于"), { target: { value: "2026-10-01T12:00" } });
    await user.click(dialog.getByRole("button", { name: "确认清理" }));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/account_pool/onboarding/items/cleanup", {
        accessToken: "test",
        body: { source: "auth_file", completed_before: new Date("2026-10-01T12:00").toISOString() },
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("applies the saved form template only when opted in, preserving auth tokens", async () => {
    const originalGet = get.getMockImplementation()!;
    get.mockImplementation(async (path: string) =>
      path === "/config/runtime"
        ? {
            version: 2,
            values: { auth_file_template: { priority: 0, prefix: "shared", proxy_profile_id: "proxy-us" } },
          }
        : originalGet(path),
    );
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByRole("checkbox", { name: "使用默认认证模板" }));
    const content = '{"email":"test@example.test","refresh_token":"fixture","prefix":"old"}';
    const file = new File([content], "account.json", { type: "application/json" });
    Object.defineProperty(file, "text", { value: async () => content });
    await user.upload(screen.getByLabelText("认证 JSON 文件（可多选）"), file);
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        "/account_pool/onboarding/preview",
        expect.objectContaining({
          body: expect.objectContaining({
            proxy_profile_id: "proxy-us",
            entries: [
              expect.objectContaining({
                content: JSON.stringify({
                  email: "test@example.test",
                  refresh_token: "fixture",
                  prefix: "shared",
                  priority: 0,
                }),
              }),
            ],
          }),
        }),
      ),
    );
  });
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

  it("shows the current Clash node and card usage, and marks the configured default proxy", async () => {
    get.mockImplementation(async (path: string) => {
      if (path.endsWith("/items")) return [item];
      if (path.endsWith("/proxy-profiles"))
        return [
          { id: "clash-gateway-7891", name: "Clash 端口 7891", protocol: "http" },
          { id: "proxy-original", name: "主站代理", protocol: "http" },
        ];
      if (path.endsWith("/proxy-gateways"))
        return [
          {
            port: 7891,
            profile_id: "clash-gateway-7891",
            name: "Clash 端口 7891",
            proxy_url: "http://proxy.internal:7891",
            current_node: "日本02",
          },
        ];
      if (path.endsWith("/environments"))
        return [
          { id: "card-1", proxy_profile_id: "clash-gateway-7891" },
          { id: "card-2", proxy_profile_id: "clash-gateway-7891" },
        ];
      if (path === "/config/runtime") return { values: { default_proxy_profile_id: "proxy-original" } };
      if (path.endsWith("/suppliers"))
        return [
          { supplier: "openai_codex", display_name: "Codex", authentication: "OAuth", oauth: true, auth_file: true },
        ];
      return [];
    });
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("tab", { name: "OAuth 上号" }));
    await user.click(await screen.findByRole("combobox", { name: "OAuth 出站代理" }));

    expect(
      await screen.findByRole("option", { name: "Clash 端口 7891 · 节点：日本02 · 同节点 2 张卡片" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: "号池默认出站代理 · 主站代理" }));

    const fileInput = screen.getByLabelText("账号 JSON 文件");
    const content = '[{"email":"account@example.com","mailbox_password":"mail-secret"}]';
    const file = new File([content], "accounts.json", { type: "application/json" });
    Object.defineProperty(file, "text", { value: async () => content });
    await user.upload(fileInput, file);

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        "/account_pool/onboarding/preview",
        expect.objectContaining({
          body: expect.objectContaining({ source: "oauth", proxy_profile_id: "proxy-original" }),
        }),
      ),
    );
  });
});
