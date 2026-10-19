import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";

import { AccountPoolCredentialsPanel } from "./AccountPoolCredentialsPanel";
import type { AccountPoolEnvironment } from "../../utils/AccountPoolTypes";

const listCredentials = vi.fn();
const patchAuthFileStatus = vi.fn();
const getAuthFileRefreshStatus = vi.fn();
const refreshAuthFiles = vi.fn();
const setAuthFileRefreshInterval = vi.fn();
const uploadAuthFile = vi.fn();
const deleteAuthFile = vi.fn();
const patchAuthFileFields = vi.fn();
const deleteCredential = vi.fn();
const getSettings = vi.fn();
const updateSettings = vi.fn();

vi.mock("../../api/AccountPoolApi", () => ({
  listAccountPoolProxyProfiles: async () => [{ id: "node-a", name: "节点 A" }],
  listAccountPoolEnvironments: async () => [],
}));

vi.mock("../../api/AccountPoolManagementApi", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api/AccountPoolManagementApi")>();
  return {
    ...original,
    getAccountPoolSettings: (...args: unknown[]) => getSettings(...args),
    updateAccountPoolSettings: (...args: unknown[]) => updateSettings(...args),
    listAccountPoolCredentials: (...args: unknown[]) => listCredentials(...args),
    patchAccountPoolAuthFileStatus: (...args: unknown[]) => patchAuthFileStatus(...args),
    getAccountPoolAuthFileRefreshStatus: (...args: unknown[]) => getAuthFileRefreshStatus(...args),
    refreshAccountPoolAuthFiles: (...args: unknown[]) => refreshAuthFiles(...args),
    setAccountPoolAuthFileRefreshInterval: (...args: unknown[]) => setAuthFileRefreshInterval(...args),
    uploadAccountPoolAuthFile: (...args: unknown[]) => uploadAuthFile(...args),
    deleteAccountPoolAuthFile: (...args: unknown[]) => deleteAuthFile(...args),
    patchAccountPoolAuthFileFields: (...args: unknown[]) => patchAuthFileFields(...args),
    deleteAccountPoolCredential: (...args: unknown[]) => deleteCredential(...args),
  };
});

const environment = {
  id: "00000000-0000-4000-8000-000000000002",
  version: 4,
  name: "plus02",
  provider: "openai",
  channel: "cliproxyapi",
  supplier: "openai_codex",
  authorization_flow: "browser_oauth",
  status: "cooling_down",
  desired_configuration_version: 0,
  observed_configuration_version: 0,
  configuration_pending: false,
  enabled: true,
  manual_cooldown: false,
  concurrency_limit: 2,
  proxy_mode: "default_gateway",
  proxy_profile_id: null,
  available_models: [],
  enabled_models: [],
  quota: { observed_at: null, plan_type: "plus", windows: [], balances: [] },
  model_quotas: [],
  cooldown_until: null,
  automatic_cooldown: true,
  last_error: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
} as AccountPoolEnvironment;

describe("AccountPoolCredentialsPanel", () => {
  beforeEach(() => {
    void i18n.changeLanguage("zh-CN");
    vi.resetAllMocks();
    getSettings.mockResolvedValue({
      version: 2,
      values: {
        default_concurrency_limit: 2,
        auth_file_template: { priority: 4, prefix: "shared", proxy_profile_id: null },
      },
    });
    updateSettings.mockResolvedValue({ version: 3, values: {} });
    listCredentials.mockResolvedValue([
      {
        id: `${environment.id}:default`,
        card_id: environment.id,
        card_name: environment.name,
        supplier: environment.supplier,
        kind: "oauth_file",
        status: "disabled",
        enabled: false,
        model_count: 0,
        auth_index: null,
        account_email: "test-account@example.test",
        account_id: "account-identity-123",
        file_name: "test-account.json",
      },
    ]);
    patchAuthFileStatus.mockResolvedValue(environment);
    patchAuthFileFields.mockResolvedValue(environment);
    deleteCredential.mockResolvedValue(environment);
    uploadAuthFile.mockResolvedValue(environment);
    getAuthFileRefreshStatus.mockResolvedValue({
      interval_minutes: 15,
      running: true,
      last_started_at: "2026-09-16T10:00:00Z",
      last_completed_at: "2026-09-16T10:01:00Z",
      next_refresh_at: "2026-09-16T10:16:00Z",
      last_failed_count: null,
    });
    refreshAuthFiles.mockResolvedValue({
      interval_minutes: 15,
      running: false,
      last_started_at: "2026-09-16T10:00:00Z",
      last_completed_at: "2026-09-16T10:01:00Z",
      next_refresh_at: "2026-09-16T10:16:00Z",
      last_failed_count: 0,
    });
    setAuthFileRefreshInterval.mockResolvedValue({
      interval_minutes: 30,
      running: false,
      last_started_at: "2026-09-16T10:00:00Z",
      last_completed_at: "2026-09-16T10:01:00Z",
      next_refresh_at: "2026-09-16T10:31:00Z",
      last_failed_count: null,
    });
  });

  it("identifies the account behind a card without exposing credential contents", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AccountPoolCredentialsPanel accessToken="token" environments={[environment]} />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("test-account@example.test")).toBeInTheDocument();
    expect(screen.getByText("account-identity-123")).toBeInTheDocument();
    expect(screen.getByText("test-account.json")).toBeInTheDocument();
  });

  it("edits explicit form fields without requiring JSON and refreshes credentials and cards", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");
    render(
      <QueryClientProvider client={queryClient}>
        <AccountPoolCredentialsPanel accessToken="token" environments={[environment]} />
      </QueryClientProvider>,
    );
    await user.click(await screen.findByRole("button", { name: /编辑认证字段|Edit auth fields/i }));
    const dialog = within(screen.getByRole("dialog"));
    await user.click(dialog.getByRole("checkbox", { name: "修改模型前缀" }));
    fireEvent.change(dialog.getByLabelText("模型前缀"), { target: { value: "team-a" } });
    await user.click(dialog.getByRole("button", { name: /^(保存|Save)$/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(patchAuthFileFields).toHaveBeenCalledWith("token", environment.id, { prefix: "team-a" });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["account-pool", "credentials", "token"] });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["account-pool", "environments"] });
  });

  it("keeps credential index and card version when deleting a direct API credential", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    listCredentials.mockResolvedValue([
      {
        id: "credential-2",
        card_id: environment.id,
        card_name: environment.name,
        kind: "api_key",
        auth_index: "2",
        enabled: true,
        supplier: environment.supplier,
        status: "ready",
        model_count: 1,
      },
    ]);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AccountPoolCredentialsPanel accessToken="token" environments={[environment]} />
      </QueryClientProvider>,
    );
    await user.click(await screen.findByRole("button", { name: /删除凭据|Remove credential/i }));
    await waitFor(() =>
      expect(deleteCredential).toHaveBeenCalledWith("token", environment.id, {
        version: environment.version,
        credential_index: 1,
      }),
    );
    expect(deleteAuthFile).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it("reloads credential data under the new token when authorization changes", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <AccountPoolCredentialsPanel accessToken="token" environments={[environment]} />
      </QueryClientProvider>,
    );
    await screen.findByText("test-account.json");
    listCredentials.mockResolvedValue([]);
    rerender(
      <QueryClientProvider client={queryClient}>
        <AccountPoolCredentialsPanel accessToken="other-token" environments={[environment]} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(listCredentials).toHaveBeenCalledWith("other-token"));
    expect(screen.queryByText("test-account.json")).not.toBeInTheDocument();
    expect(getAuthFileRefreshStatus).toHaveBeenCalledWith("other-token");
  });

  it("shows and updates authentication refresh controls", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");
    render(
      <QueryClientProvider client={queryClient}>
        <AccountPoolCredentialsPanel accessToken="token" environments={[environment]} />
      </QueryClientProvider>,
    );

    expect(
      await screen.findByRole("combobox", { name: /Authentication refresh interval|认证刷新档位/i }),
    ).toHaveTextContent(/15/);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /Refresh authentication files|刷新认证文件/i }).querySelector("svg"),
      ).toHaveClass("animate-spin"),
    );
    await user.click(screen.getByRole("combobox", { name: /Authentication refresh interval|认证刷新档位/i }));
    expect(await screen.findByRole("option", { name: /Every 5 minutes|每 5 分钟/i })).toBeVisible();
    expect(screen.getByRole("option", { name: /Every 15 minutes|每 15 分钟/i })).toBeVisible();
    expect(screen.getByRole("option", { name: /Every 30 minutes|每 30 分钟/i })).toBeVisible();
    expect(screen.getByRole("option", { name: /Every 60 minutes|每 60 分钟/i })).toBeVisible();
    await user.click(screen.getByRole("option", { name: /Every 30 minutes|每 30 分钟/i }));

    await waitFor(() => expect(setAuthFileRefreshInterval).toHaveBeenCalledWith("token", 30));
    await user.click(screen.getByRole("button", { name: /Refresh authentication files|刷新认证文件/i }));

    await waitFor(() => expect(refreshAuthFiles).toHaveBeenCalledWith("token"));
    await waitFor(() =>
      expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["account-pool", "auth-file-refresh", "token"] }),
    );
    expect(screen.getByText(/Last completed refresh|上次完成刷新/i).parentElement).toHaveTextContent(/09\/16/);
    expect(screen.getByText(/Next scheduled refresh|下次计划刷新/i).parentElement).toHaveTextContent(/09\/16/);
  });

  it("enables a disabled auth file even when its card remains enabled", async () => {
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AccountPoolCredentialsPanel accessToken="token" environments={[environment]} />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: /启用|Enable/i }));

    await waitFor(() => expect(patchAuthFileStatus).toHaveBeenCalledWith("token", environment.id, false));
  });

  it("replaces the selected card's file without a separate delete request", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");
    render(
      <QueryClientProvider client={queryClient}>
        <AccountPoolCredentialsPanel
          accessToken="token"
          environments={[{ ...environment, id: "other-card", name: "Other card" }, environment]}
        />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: /Replace file|更换文件/i }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.queryByRole("combobox", { name: /Target card|目标卡片/i })).not.toBeInTheDocument();
    expect(dialog.getByText(environment.name)).toBeVisible();
    expect(dialog.getByText(/Current file: test-account.json|当前文件：test-account.json/)).toBeVisible();
    expect(dialog.getByRole("button", { name: /Confirm replacement|确认更换/i })).toBeDisabled();
    const file = new File(['{"refresh_token":"test-replacement"}'], "replacement.json", { type: "application/json" });
    await user.upload(dialog.getByLabelText(/^(Auth file|认证文件)$/i), file);
    await user.click(dialog.getByRole("button", { name: /Confirm replacement|确认更换/i }));

    await waitFor(() => expect(uploadAuthFile).toHaveBeenCalledWith("token", environment.id, file, true));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(deleteAuthFile).not.toHaveBeenCalled();
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["account-pool", "credentials", "token"] });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["account-pool", "environments"] });
  });

  it("discards the selected file on cancel and allows regular uploads to choose a card", async () => {
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AccountPoolCredentialsPanel accessToken="token" environments={[environment]} />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: /Replace file|更换文件/i }));
    await user.upload(screen.getByLabelText(/^(Auth file|认证文件)$/i), new File(["{}"], "cancelled.json"));
    await user.click(screen.getByRole("button", { name: /Cancel|取消/i }));
    await user.click(screen.getByRole("button", { name: /Replace file|更换文件/i }));
    expect(screen.getByLabelText(/^(Auth file|认证文件)$/i)).toHaveValue("");
    expect(screen.getByRole("button", { name: /Confirm replacement|确认更换/i })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Cancel|取消/i }));
    await user.click(screen.getByRole("button", { name: /Upload auth file|上传认证文件/i }));
    expect(within(screen.getByRole("dialog")).getByRole("combobox", { name: /Target card|目标卡片/i })).toBeEnabled();
    expect(uploadAuthFile).not.toHaveBeenCalled();
    expect(deleteAuthFile).not.toHaveBeenCalled();
  });

  it("locks the form while replacing and keeps failures open for retry with refreshed card state", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");
    const pending = Promise.withResolvers<AccountPoolEnvironment>();
    uploadAuthFile.mockReturnValueOnce(pending.promise);
    render(
      <QueryClientProvider client={queryClient}>
        <AccountPoolCredentialsPanel accessToken="token" environments={[environment]} />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: /Replace file|更换文件/i }));
    const file = new File(["{}"], "retry.json", { type: "application/json" });
    await user.upload(screen.getByLabelText(/^(Auth file|认证文件)$/i), file);
    await user.click(screen.getByRole("button", { name: /Confirm replacement|确认更换/i }));
    expect(screen.getByLabelText(/^(Auth file|认证文件)$/i)).toBeDisabled();
    expect(screen.getByRole("button", { name: /Cancel|取消/i })).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeVisible();
    pending.reject(new Error("Validation failed"));

    expect(await within(screen.getByRole("dialog")).findByRole("alert")).toHaveTextContent("Validation failed");
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["account-pool", "environments"] });
    expect(screen.getByLabelText(/^(Auth file|认证文件)$/i)).toBeEnabled();
    await user.click(screen.getByRole("button", { name: /Confirm replacement|确认更换/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(uploadAuthFile).toHaveBeenCalledTimes(2);
    expect(uploadAuthFile).toHaveBeenLastCalledWith("token", environment.id, file, true);
    expect(deleteAuthFile).not.toHaveBeenCalled();
  });

  it("prevents adding a second credential and sends uploads only to an explicitly selected empty card", async () => {
    const user = userEvent.setup();
    const emptyCard = {
      ...environment,
      id: "empty-card",
      name: "Empty card",
      status: "error",
    } as AccountPoolEnvironment;
    uploadAuthFile.mockRejectedValueOnce(new Error("该账号或凭证已绑定其他卡片"));
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AccountPoolCredentialsPanel accessToken="token" environments={[environment, emptyCard]} />
      </QueryClientProvider>,
    );
    await screen.findByText("test-account.json");
    await user.click(screen.getByRole("button", { name: /Upload auth file|上传认证文件/i }));
    const dialog = within(screen.getByRole("dialog"));
    const target = dialog.getByRole("combobox", { name: /Target card|目标卡片/i });
    expect(target).toHaveValue("");
    expect(dialog.getByRole("option", { name: /plus02/ })).toBeDisabled();
    const file = new File(["{}"], "a-long-auth-file-name-that-must-stay-separate-from-the-button.json");
    await user.upload(dialog.getByLabelText(/^(Auth file|认证文件)$/i), file);
    expect(dialog.getByText(file.name)).toBeVisible();
    expect(dialog.getByRole("button", { name: /Upload auth file|上传认证文件/i })).toBeDisabled();
    await user.selectOptions(target, emptyCard.id);
    await user.click(dialog.getByRole("button", { name: /Upload auth file|上传认证文件/i }));
    expect(await dialog.findByRole("alert")).toHaveTextContent("该账号或凭证已绑定其他卡片");
    expect(uploadAuthFile).toHaveBeenCalledWith("token", emptyCard.id, file, false);
    expect(screen.getByText("test-account.json")).toBeVisible();
    expect(deleteAuthFile).not.toHaveBeenCalled();
  });

  it("searches and filters credentials before paginating compact rows", async () => {
    const user = userEvent.setup();
    const base = {
      card_id: environment.id,
      supplier: "openai_codex",
      kind: "oauth_file",
      enabled: true,
      status: "ready",
      model_count: 1,
    };
    listCredentials.mockResolvedValue(
      Array.from({ length: 21 }, (_, index) => ({
        ...base,
        id: `credential-${index}`,
        card_name: `Card ${index}`,
        account_email: `account-${index}@example.test`,
        file_name: `file-${index}.json`,
      })),
    );
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AccountPoolCredentialsPanel accessToken="token" environments={[environment]} />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("file-0.json")).toBeVisible();
    expect(screen.queryByText("file-20.json")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "下一页" }));
    expect(screen.getByText("file-20.json")).toBeVisible();
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索认证文件" }), { target: { value: "account-3@" } });
    expect(screen.getByText("file-3.json")).toBeVisible();
    expect(screen.queryByText("file-20.json")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("认证状态筛选"), "disabled");
    expect(screen.getByText("没有匹配的认证文件")).toBeVisible();
  });

  it("saves a form template on the server and applies only chosen fields to selected credentials", async () => {
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AccountPoolCredentialsPanel accessToken="token" environments={[environment]} />
      </QueryClientProvider>,
    );
    await user.click(await screen.findByRole("checkbox", { name: "选择 plus02" }));
    await user.click(screen.getByRole("button", { name: "批量设置（1）" }));
    const dialog = within(screen.getByRole("dialog"));
    await user.click(dialog.getByRole("button", { name: "载入默认模板" }));
    expect(dialog.getByLabelText("优先级")).toHaveValue(4);
    expect(dialog.getByLabelText("模型前缀")).toHaveValue("shared");
    fireEvent.change(dialog.getByLabelText("模型前缀"), { target: { value: "team-b" } });
    await user.click(dialog.getByRole("button", { name: "保存为默认模板" }));
    await waitFor(() =>
      expect(updateSettings).toHaveBeenCalledWith(
        "token",
        {
          version: 2,
          values: {
            default_concurrency_limit: 2,
            auth_file_template: { priority: 4, prefix: "team-b", proxy_profile_id: null },
          },
        },
        "common",
      ),
    );
    expect(patchAuthFileFields).not.toHaveBeenCalled();
    await user.click(dialog.getByRole("button", { name: "保存", exact: true }));
    await waitFor(() =>
      expect(patchAuthFileFields).toHaveBeenCalledWith("token", environment.id, { priority: 4, prefix: "team-b" }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("locks field choices while applying and retries only failed cards with the same fields", async () => {
    const user = userEvent.setup();
    const second = { ...environment, id: "00000000-0000-4000-8000-000000000003", name: "second-card" };
    listCredentials.mockResolvedValue(
      [environment, second].map((card) => ({
        id: `${card.id}:default`,
        card_id: card.id,
        card_name: card.name,
        supplier: "openai_codex",
        kind: "oauth_file",
        enabled: true,
        status: "ready",
        model_count: 1,
        file_name: `${card.name}.json`,
      })),
    );
    let finishFirst!: (value: AccountPoolEnvironment) => void;
    patchAuthFileFields
      .mockImplementationOnce(
        () =>
          new Promise<AccountPoolEnvironment>((resolve) => {
            finishFirst = resolve;
          }),
      )
      .mockRejectedValueOnce(new Error("fixture unavailable"))
      .mockResolvedValueOnce(second);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AccountPoolCredentialsPanel accessToken="token" environments={[environment, second]} />
      </QueryClientProvider>,
    );
    await screen.findByText("second-card.json");
    await user.click(await screen.findByRole("checkbox", { name: /全选本页认证文件/ }));
    await user.click(screen.getByRole("button", { name: "批量设置（2）" }));
    const dialog = within(screen.getByRole("dialog"));
    await user.click(dialog.getByRole("button", { name: "载入默认模板" }));
    await user.click(dialog.getByRole("button", { name: "应用到 2 项" }));
    await waitFor(() => expect(patchAuthFileFields).toHaveBeenCalledTimes(1));
    const prefixChoice = dialog.getByRole("checkbox", { name: "修改模型前缀" });
    fireEvent.click(prefixChoice);
    expect(prefixChoice).toBeChecked();
    expect(prefixChoice).toHaveAttribute("aria-disabled", "true");
    finishFirst(environment);
    expect(await dialog.findByRole("status")).toHaveTextContent("失败 1 项：second-card");
    fireEvent.click(prefixChoice);
    expect(prefixChoice).toBeChecked();
    expect(dialog.getByLabelText("模型前缀")).toBeDisabled();
    await user.click(dialog.getByRole("button", { name: "应用到 1 项" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(patchAuthFileFields).toHaveBeenCalledTimes(3);
    expect(patchAuthFileFields).toHaveBeenNthCalledWith(1, "token", environment.id, { priority: 4, prefix: "shared" });
    expect(patchAuthFileFields).toHaveBeenNthCalledWith(2, "token", second.id, { priority: 4, prefix: "shared" });
    expect(patchAuthFileFields).toHaveBeenNthCalledWith(3, "token", second.id, { priority: 4, prefix: "shared" });
  });
});
