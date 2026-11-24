/** 从真实组件树验证授权任务表单、设置门禁及验收重试，只替换网络边界。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { AccountPoolAutomationPanel } from "./AccountPoolAutomationPanel";
import type { AutomationJob, AutomationView } from "../../api/AccountPoolAutomationApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import { ApiError } from "@/lib/http/client";
import i18n from "@/i18n";

const { get, post, put, del, toastError } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
  toastError: vi.fn(),
}));
vi.mock("@/components/networking", () => ({ apiClient: { get, post, put, delete: del }, serverRootPath: "" }));
vi.mock("@/lib/toast", () => ({ toast: { success: vi.fn(), error: toastError } }));
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
  draft_pending: false,
  cleanup_pending: false,
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
const mount = (initialEnvironmentId?: string) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AccountPoolAutomationPanel accessToken="test-admin" initialEnvironmentId={initialEnvironmentId} />
    </QueryClientProvider>,
  );
  return client;
};
beforeEach(async () => {
  await i18n.changeLanguage("zh-CN");
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
  del.mockResolvedValue(view);
});

it("renders the authorization queue controls in English", async () => {
  await i18n.changeLanguage("en");
  mount();
  expect(await screen.findByLabelText("Blank Codex card")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Add to queue" })).toBeInTheDocument();
});

const orphanProfile: AutomationView["profiles"][number] = {
  id: "orphan-profile",
  name: "旧卡指纹",
  version: 1,
  enabled: true,
  locale: "en-US",
  auto_match_proxy: false,
  os: "linux",
  digest: "a".repeat(64),
  bound_environment_id: "deleted-card",
  login_saved: false,
  reservation: false,
};

it.each([
  ["ja-JP", "accepted"],
  ["pt-BR", "denied"],
  ["pt-BR", "screen-unavailable"],
] as const)("shows the job profile language %s and opens its screen only after manual acknowledgement (%s)", async (locale, outcome) => {
  const waiting = { ...job, stage: "waiting", message: "上游要求邮箱验证", callback_consumed: false, credentials_saved: false };
  const profile = { ...orphanProfile, id: job.profile_id, auto_match_proxy: true, matched_locale: locale };
  get.mockImplementation(async (path: string) =>
    path.endsWith("/oauth-automation") ? { ...view, profiles: [profile], jobs: [waiting] } : [{ id: job.environment_id }],
  );
  post.mockImplementation(async (path: string) => {
    if (path.endsWith("/action")) {
      if (outcome === "denied") throw new ApiError("当前会话无法接管", 409, {});
      return { ...waiting, stage: "manual" };
    }
    if (path.endsWith("/screen")) {
      if (outcome === "screen-unavailable") throw new Error("private-upstream-detail");
      return { id: "session-fixture", ticket: "private-screen-ticket" };
    }
  });
  mount();
  const user = userEvent.setup();
  expect(await screen.findByText(`配置语言 ${locale}`)).toBeVisible();
  expect(screen.getByText(/Chat 未验收，Responses 未验收/)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "停止脚本并接管" }));
  expect(post.mock.calls[0][1].body).toEqual({ action: "takeover" });
  if (outcome === "accepted") {
    expect(await screen.findByTitle("授权浏览器画面")).toHaveAttribute("src", expect.stringContaining("/session-fixture/"));
    expect(post.mock.calls.map(([path]) => path.split("/").pop())).toEqual(["action", "screen", "browser"]);
    expect(screen.getByTitle("授权浏览器画面").getAttribute("src")).not.toContain("private-screen-ticket");
  } else {
    const denied = outcome === "denied";
    expect(await screen.findByRole("alert")).toHaveTextContent(denied ? "当前会话无法接管" : "已接管，但画面未能打开");
    expect(post).toHaveBeenCalledTimes(denied ? 1 : 2);
    expect(screen.queryByTitle("授权浏览器画面")).not.toBeInTheDocument();
  }
});

it("confirms deletion of an orphaned binding but never silently deletes it", async () => {
  get.mockImplementation(async (path: string) =>
    path.endsWith("/oauth-automation") ? { ...view, profiles: [orphanProfile] } : [],
  );
  const user = userEvent.setup();
  mount();
  await user.click(await screen.findByRole("tab", { name: /指纹配置/ }));
  await user.click(await screen.findByRole("button", { name: "删除失效绑定" }));
  expect(screen.getByRole("dialog", { name: "确认删除指纹" })).toBeVisible();
  expect(del).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "取消" }));
  expect(del).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "删除失效绑定" }));
  await user.click(screen.getByRole("button", { name: "确认删除" }));
  await waitFor(() =>
    expect(del).toHaveBeenCalledWith("/account_pool/oauth-automation/profiles/orphan-profile", {
      accessToken: "test-admin",
    }),
  );
});

it("keeps fingerprints bound to existing cards protected", async () => {
  get.mockImplementation(async (path: string) =>
    path.endsWith("/oauth-automation")
      ? { ...view, profiles: [{ ...orphanProfile, bound_environment_id: "card-001" }] }
      : [{ id: "card-001" }],
  );
  const user = userEvent.setup();
  mount();
  await user.click(await screen.findByRole("tab", { name: /指纹配置/ }));
  expect(await screen.findByRole("button", { name: "删除指纹" })).toBeDisabled();
  expect(del).not.toHaveBeenCalled();
});

it.each(["queued", "running"] as const)("protects orphaned profiles while a task is %s", async (stage) => {
  get.mockImplementation(async (path: string) =>
    path.endsWith("/oauth-automation")
      ? { ...view, profiles: [orphanProfile], jobs: [{ ...job, profile_id: orphanProfile.id, stage }] }
      : [],
  );
  const user = userEvent.setup();
  mount();
  await user.click(await screen.findByRole("tab", { name: /指纹配置/ }));
  expect(await screen.findByRole("button", { name: "删除失效绑定" })).toBeDisabled();
});

it("does not treat an unavailable card list as proof that a bound card was deleted", async () => {
  get.mockImplementation(async (path: string) => {
    if (path.endsWith("/oauth-automation")) return { ...view, profiles: [orphanProfile] };
    throw new Error("card inventory unavailable");
  });
  const user = userEvent.setup();
  mount();
  await user.click(await screen.findByRole("tab", { name: /指纹配置/ }));
  expect(await screen.findByRole("button", { name: "删除指纹" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "删除失效绑定" })).not.toBeInTheDocument();
});

it("disables old job retries when their card has been deleted", async () => {
  get.mockImplementation(async (path: string) =>
    path.endsWith("/oauth-automation")
      ? { ...view, jobs: [{ ...job, stage: "failed", credentials_saved: false, callback_consumed: false }] }
      : [],
  );
  mount();
  expect(await screen.findByRole("button", { name: "新会话重试" })).toBeDisabled();
  expect(screen.getByText(/原卡片已删除，请使用新卡提交新任务/)).toBeVisible();
});

it("explains rejected submissions and retains credentials only in the form", async () => {
  const message = "原卡片已删除，请到指纹配置确认删除失效绑定后再提交";
  post.mockRejectedValue(new ApiError(message, 409, { detail: message }));
  const user = userEvent.setup();
  mount("card-001");
  await screen.findByRole("option", { name: "空白测试卡" });
  const credentials = screen.getByRole("textbox", { name: "单账号资料" });
  fireEvent.change(credentials, { target: { value: "one@example.test----private-password----GEZDGNBVGY3TQOJQ" } });
  await user.click(screen.getByRole("button", { name: "添加到串行队列" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(message);
  expect(credentials).toHaveValue("one@example.test----private-password----GEZDGNBVGY3TQOJQ");
  expect(toastError).toHaveBeenCalledWith(message);
});

it("does not expose unexpected upstream error bodies in the form", async () => {
  post.mockRejectedValue(new ApiError("private-upstream-token", 502, { detail: "private-upstream-token" }));
  const user = userEvent.setup();
  mount("card-001");
  await screen.findByRole("option", { name: "空白测试卡" });
  fireEvent.change(screen.getByRole("textbox", { name: "单账号资料" }), { target: { value: "fixture credentials" } });
  await user.click(screen.getByRole("button", { name: "添加到串行队列" }));
  expect(await screen.findByRole("alert")).not.toHaveTextContent("private-upstream-token");
  expect(toastError).not.toHaveBeenCalledWith(expect.stringContaining("private-upstream-token"));
});

it("preselects the OAuth entry card without submitting credentials or enabling automation", async () => {
  mount("card-001");
  await screen.findByRole("option", { name: "空白测试卡" });
  expect(screen.getByRole("combobox", { name: "空白 Codex 卡片" })).toHaveValue("card-001");
  expect(screen.getByRole("textbox", { name: "单账号资料" })).toHaveValue("");
  expect(post).not.toHaveBeenCalled();
  expect(put).not.toHaveBeenCalled();
});

it("does not silently select a different account when the linked card is unavailable", async () => {
  mount("missing-card");
  await screen.findByRole("option", { name: "空白测试卡" });
  expect(screen.getByRole("combobox", { name: "空白 Codex 卡片" })).toHaveValue("");
  expect(screen.getByText(/所选卡片不满足/)).toBeVisible();
  fireEvent.change(screen.getByRole("textbox", { name: "单账号资料" }), { target: { value: "test credentials" } });
  expect(screen.getByRole("button", { name: "添加到串行队列" })).toBeDisabled();
  expect(post).not.toHaveBeenCalled();
});

it("clears submitted credentials after acknowledgement and never puts them in query or mutation caches", async () => {
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

it("populates language choices from the Manager and sends the selected code without rewriting it", async () => {
  get.mockImplementation(async (path: string) =>
    path.endsWith("/oauth-automation")
      ? { ...view, image_checked: true, locale_presets: ["en-US", "ja-JP", "uk-UA", "es-AR", "it-IT"] }
      : [],
  );
  const user = userEvent.setup();
  mount();
  await user.click(await screen.findByRole("tab", { name: /指纹配置/ }));
  const language = screen.getByRole("combobox", { name: "浏览器语言" });
  expect(screen.getAllByRole("option").some((option) => option.getAttribute("value") === "es-AR")).toBe(true);
  await user.selectOptions(language, "it-IT");
  await user.type(screen.getByRole("textbox", { name: "配置名称" }), "locale fixture");
  await user.click(screen.getByRole("button", { name: "生成并验证" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/account_pool/oauth-automation/profiles", {
      accessToken: "test-admin",
      body: { name: "locale fixture", locale: "it-IT", enabled: true, auto_match_proxy: false, os: "linux" },
    }),
  );
});

it("offers verification-only retry for saved credentials, never a new-login retry", async () => {
  get.mockImplementation(async (path: string) =>
    path.endsWith("/oauth-automation") ? { ...view, jobs: [job] } : [{ id: job.environment_id }],
  );
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

it("confirms and removes a finished job without acting on its card", async () => {
  let jobs = [
    { ...job, stage: "completed" as const },
    { ...job, id: "active-job", stage: "running" as const },
  ];
  get.mockImplementation(async (path: string) => (path.endsWith("/oauth-automation") ? { ...view, jobs } : []));
  del.mockImplementation(async () => {
    jobs = jobs.filter((row) => row.id !== job.id);
    return { ...view, jobs };
  });
  mount();
  const user = userEvent.setup();
  const buttons = await screen.findAllByRole("button", { name: "删除记录" });
  expect(buttons).toHaveLength(1);
  await user.click(buttons[0]);
  expect(del).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "确认删除记录" }));
  await waitFor(() =>
    expect(del).toHaveBeenCalledWith("/account_pool/oauth-automation/jobs/job-001", {
      accessToken: "test-admin",
    }),
  );
  await waitFor(() => expect(screen.queryByRole("article", { name: "任务 job-001" })).not.toBeInTheDocument());
  expect(post).not.toHaveBeenCalled();
});

it("submits one durable operation for the new Codex card and its authorization", async () => {
  const credentials = "fixture@example.test----test-password----GEZDGNBVGY3TQOJQ";
  get.mockImplementation(async (path: string) => {
    if (path.endsWith("/oauth-automation")) return view;
    if (path.endsWith("/proxy-profiles"))
      return [{ id: "proxy-001", name: "测试代理", type: "http", url: "http://local" }];
    if (path.endsWith("/settings")) return { values: {} };
    return [];
  });
  mount();
  const user = userEvent.setup();
  await user.selectOptions(await screen.findByLabelText("空白 Codex 卡片"), "__new__");
  fireEvent.change(screen.getByLabelText("单账号资料"), { target: { value: credentials } });
  await user.selectOptions(await screen.findByLabelText("OAuth 出站代理"), "proxy-001");
  await user.click(screen.getByRole("button", { name: "创建卡片并加入队列" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0][0]).toBe("/account_pool/oauth-automation/jobs");
  expect(post.mock.calls[0][1].body).toMatchObject({
    environment_id: post.mock.calls[0][1].body.id,
    credentials,
    draft: { name: "Codex-套餐待识别-fixture@example.test", proxy_profile_id: "proxy-001" },
  });
  expect(screen.getByLabelText("单账号资料")).toHaveValue("");
});

it("offers a readback retry rather than model-only retry when no credential was confirmed", async () => {
  get.mockImplementation(async (path: string) =>
    path.endsWith("/oauth-automation") ? { ...view, jobs: [{ ...job, credentials_saved: false, draft_pending: true }] } : [],
  );
  mount();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "核对凭据后重试" }));
  expect(post).toHaveBeenCalledWith("/account_pool/oauth-automation/jobs/job-001/action", {
    accessToken: "test-admin", body: { action: "retry" },
  });
  expect(screen.queryByRole("button", { name: "仅重试验收" })).not.toBeInTheDocument();
});

it.each([
  ["2026-11-04T12:00:30Z", "2026-11-04T12:10:00Z", 30],
  [null, "2026-11-04T12:10:00Z", 600],
  ["2026-11-04T11:59:59Z", "2026-11-04T12:10:00Z", 0],
  [null, null, null],
])("displays the remaining authorization deadline %s / %s as %s", async (humanDeadline, deadline, seconds) => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-04T12:00:00Z"));
  try {
    const running = { ...job, stage: "running", human_expires_at: humanDeadline, expires_at: deadline };
    get.mockImplementation(async (path: string) =>
      path.endsWith("/oauth-automation") ? { ...view, jobs: [running] } : [],
    );
    mount();
    await screen.findByText("自动登录 / 2FA");
    if (seconds === null) {
      expect(screen.queryByText(/^剩余 \d+ 秒$/)).not.toBeInTheDocument();
    } else {
      expect(screen.getByText(`剩余 ${seconds} 秒`)).toBeVisible();
    }
  } finally {
    vi.useRealTimers();
  }
});


it.each([422, 502])("shows safe %s details and preserves a rejected submission for correction", async (status) => {
  const message = status === 422 ? "登录资料格式错误" : "浏览器镜像自检失败";
  post.mockRejectedValue(new ApiError(message, status, { detail: { code: status === 422 ? "invalid_credentials" : "image_check_failed", message } }));
  const user = userEvent.setup();
  const client = mount("card-001");
  await screen.findByRole("option", { name: "空白测试卡" });
  const credentials = screen.getByRole("textbox", { name: "单账号资料" });
  fireEvent.change(credentials, { target: { value: "fixture-private-credentials" } });
  await user.click(screen.getByRole("button", { name: "添加到串行队列" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(message);
  expect(credentials).toHaveValue("fixture-private-credentials");
  const firstId = post.mock.calls[0][1].body.id;
  await user.click(screen.getByRole("button", { name: "添加到串行队列" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(post.mock.calls[1][1].body.id).toBe(firstId);
  expect(JSON.stringify(client.getQueryCache().getAll())).not.toContain("fixture-private-credentials");
  expect(client.getMutationCache().getAll()).toHaveLength(0);
});

it("does not erase credentials before acknowledgement or overwrite a newly edited form", async () => {
  let acknowledge!: (value: AutomationJob) => void;
  post.mockImplementation(() => new Promise<AutomationJob>((resolve) => { acknowledge = resolve; }));
  const user = userEvent.setup();
  mount("card-001");
  await screen.findByRole("option", { name: "空白测试卡" });
  const credentials = screen.getByRole("textbox", { name: "单账号资料" });
  fireEvent.change(credentials, { target: { value: "fixture-original" } });
  await user.click(screen.getByRole("button", { name: "添加到串行队列" }));
  expect(credentials).toHaveValue("fixture-original");
  fireEvent.change(credentials, { target: { value: "fixture-new-input" } });
  acknowledge(job);
  await waitFor(() => expect(screen.getByRole("button", { name: "添加到串行队列" })).toBeEnabled());
  expect(credentials).toHaveValue("fixture-new-input");
});


it("does not trust arbitrary structured upstream errors", async () => {
  post.mockRejectedValue(new ApiError("private-upstream-token", 502, {
    detail: { code: "unknown_upstream", message: "private-upstream-token" },
  }));
  const user = userEvent.setup();
  mount("card-001");
  await screen.findByRole("option", { name: "空白测试卡" });
  fireEvent.change(screen.getByRole("textbox", { name: "单账号资料" }), { target: { value: "fixture credentials" } });
  await user.click(screen.getByRole("button", { name: "添加到串行队列" }));
  expect(await screen.findByRole("alert")).not.toHaveTextContent("private-upstream-token");
});
