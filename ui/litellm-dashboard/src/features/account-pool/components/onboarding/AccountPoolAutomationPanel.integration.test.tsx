/** 从真实组件树验证授权任务表单、设置门禁及验收重试，只替换网络边界。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { AccountPoolAutomationPanel } from "./AccountPoolAutomationPanel";
import type { AutomationJob, AutomationView } from "../../api/AccountPoolAutomationApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";

const { get, post, put } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock("@/components/networking", () => ({ apiClient: { get, post, put }, serverRootPath: "" }));
const view: AutomationView = {
  version: 1,
  settings: { enabled: false, total_timeout_seconds: 600, human_timeout_seconds: 300 },
  profiles: [],
  jobs: [],
  checked_at: null,
  image_checked: false,
};
const job: AutomationJob = {
  id: "job-001",
  environment_id: "card-001",
  profile_id: "profile-001",
  stage: "verification_failed",
  message: "凭据已保存，仅重试验收",
  proxy_profile_id: "proxy-001",
  configuration_version: 3,
  browser_session_id: null,
  callback_consumed: true,
  credentials_saved: true,
  model: "available-model",
  chat_verified: false,
  responses_verified: false,
  cancel_requested: false,
  attempt: 1,
  created_at: "2026-11-04T12:00:00Z",
  updated_at: "2026-11-04T12:00:00Z",
  expires_at: null,
  human_expires_at: null,
};
const mount = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AccountPoolAutomationPanel accessToken="test-admin" />
    </QueryClientProvider>,
  );
  return client;
};
beforeEach(() => {
  vi.clearAllMocks();
  get.mockImplementation(async (path: string) =>
    path.endsWith("/oauth-automation")
      ? view
      : [
          {
            id: "card-001",
            name: "空白测试卡",
            supplier: "openai_codex",
            authorization_flow: "browser_oauth",
            enabled: true,
            proxy_profile_id: "proxy-001",
            configuration_pending: false,
            status: "awaiting_authorization",
          },
        ],
  );
  post.mockResolvedValue(job);
  put.mockResolvedValue(view);
});

it("clears submitted credentials immediately and never puts them in query or mutation caches", async () => {
  const user = userEvent.setup();
  const client = mount();
  const credentials = await screen.findByRole("textbox", { name: "单账号资料" });
  await screen.findByRole("option", { name: "空白测试卡" });
  fireEvent.change(screen.getByRole("combobox", { name: "空白 Codex 卡片" }), { target: { value: "card-001" } });
  fireEvent.change(credentials, { target: { value: "one@example.test----private-password----GEZDGNBVGY3TQOJQ" } });
  await user.click(screen.getByRole("button", { name: "添加到串行队列" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(
      "/account_pool/oauth-automation/jobs",
      expect.objectContaining({ body: expect.objectContaining({ environment_id: "card-001" }) }),
    ),
  );
  expect(credentials).toHaveValue("");
  expect(JSON.stringify(client.getQueryData(accountPoolQueryKeys.automation("test-admin")))).not.toContain(
    "private-password",
  );
  expect(client.getMutationCache().getAll()).toHaveLength(0);
});

it("keeps settings behind their own tab and reports the current image self-check gate", async () => {
  const user = userEvent.setup();
  mount();
  await user.click(await screen.findByRole("tab", { name: /运行设置/ }));
  expect(screen.getByText("当前镜像未通过自检")).toBeVisible();
  expect(screen.getByRole("checkbox", { name: "开启授权自动化" })).not.toBeChecked();
  expect(screen.getByRole("spinbutton", { name: "总时限（秒）" })).toHaveValue(600);
  await user.click(screen.getByRole("button", { name: "自检浏览器镜像" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/account_pool/oauth-automation/self-check", { accessToken: "test-admin" }),
  );
});

it.each(["linux", "windows", "macos"])(
  "saves %s with proxy matching and preserves manual language selection",
  async (os) => {
    get.mockImplementation(async (path: string) =>
      path.endsWith("/oauth-automation") ? { ...view, image_checked: true } : [],
    );
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByRole("tab", { name: /指纹配置/ }));
    const language = screen.getByRole("combobox", { name: "浏览器语言" });
    expect(language).toHaveValue("auto");
    const system = screen.getByRole("combobox", { name: "浏览器系统" });
    expect(system).toHaveValue("linux");
    fireEvent.change(system, { target: { value: os } });
    fireEvent.change(screen.getByRole("textbox", { name: "配置名称" }), { target: { value: "代理自动匹配" } });
    await user.click(screen.getByRole("button", { name: "生成并验证" }));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/account_pool/oauth-automation/profiles", {
        accessToken: "test-admin",
        body: { name: "代理自动匹配", locale: "en-US", enabled: true, auto_match_proxy: true, os },
      }),
    );
    fireEvent.change(language, { target: { value: "zh-CN" } });
    await user.click(screen.getByRole("button", { name: "生成并验证" }));
    await waitFor(() =>
      expect(post).toHaveBeenLastCalledWith("/account_pool/oauth-automation/profiles", {
        accessToken: "test-admin",
        body: { name: "代理自动匹配", locale: "zh-CN", enabled: true, auto_match_proxy: false, os },
      }),
    );
  },
);

it("offers verification-only retry for saved credentials, never a new-login retry", async () => {
  get.mockImplementation(async (path: string) => (path.endsWith("/oauth-automation") ? { ...view, jobs: [job] } : []));
  const user = userEvent.setup();
  mount();
  const model = await screen.findByRole("textbox", { name: "重试验收模型" });
  fireEvent.change(model, { target: { value: "another-enabled-model" } });
  await user.click(await screen.findByRole("button", { name: "仅重试验收" }));
  expect(screen.queryByRole("button", { name: "新会话重试" })).not.toBeInTheDocument();
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/account_pool/oauth-automation/jobs/job-001/action", {
      accessToken: "test-admin",
      body: { action: "retry", model: "another-enabled-model" },
    }),
  );
});
