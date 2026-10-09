import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18n from "@/i18n";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccountPoolSettingsOverview } from "../dashboard/AccountPoolSettingsOverview";

const endpoint = "/account_pool/releases/auto-update";
const initial = {
  enabled: false,
  interval_minutes: 5,
  revision: 2,
  status: "disabled",
  message: "自动更新已关闭",
  last_checked_at: null,
  next_check_at: null,
  candidate_commit: null,
  current_commit: "a".repeat(40),
  last_job_id: null,
  branch: "CLIProxyAPI分支",
  database_backups_enabled: true,
};
let state = { ...initial };
let failure = 0;
let getFailure = 0;
let requests: { path: string; body: unknown; authorization: string | null }[];
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const rendered = render(
    <QueryClientProvider client={client}>
      <AccountPoolSettingsOverview accessToken="admin-token" />
    </QueryClientProvider>,
  );
  return { client, ...rendered };
}

describe("automatic image updates in settings", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("zh-CN");
    state = { ...initial };
    failure = 0;
    getFailure = 0;
    requests = [];
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async (input, init) => {
        const path = new URL(String(input), "http://localhost").pathname;
        if (path === endpoint && init?.method === "GET")
          return getFailure ? json({ detail: "Not Found" }, getFailure) : json(state);
        if (path.startsWith(endpoint) && init?.method === "POST") {
          const body = init.body ? JSON.parse(String(init.body)) : undefined;
          requests.push({ path, body, authorization: new Headers(init.headers).get("Authorization") });
          if (failure) return json({ detail: failure === 409 ? "设置版本已过期" : "部署服务不可用" }, failure);
          if (path.endsWith("/check")) return json({ ...state, status: "checking", message: "正在检查 GHCR 镜像" });
          state = { ...state, ...body, revision: state.revision + 1, status: body.enabled ? "waiting" : "disabled" };
          return json(state);
        }
        if (path === "/config/runtime") return json({ version: 1, values: {}, requires_reload: false });
        if (path === "/router/settings") return json({ fields: [] });
        if (path === "/config/list") return json([]);
        throw new Error(`Unexpected request: ${init?.method} ${path}`);
      }),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it("requires acknowledgement before saving an enabled schedule through the authenticated API", async () => {
    const user = userEvent.setup();
    show();
    await user.click(await screen.findByRole("switch", { name: "自动部署新镜像" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "检查间隔（分钟）" }), { target: { value: "30" } });
    expect(screen.getByRole("button", { name: "保存自动更新设置" })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /我确认自动部署/ }));
    await user.click(screen.getByRole("button", { name: "保存自动更新设置" }));
    expect(await screen.findByText("自动更新设置已保存")).toBeInTheDocument();
    expect(requests).toEqual([
      {
        path: endpoint,
        authorization: "Bearer admin-token",
        body: { enabled: true, interval_minutes: 30, revision: 2, acknowledge_downtime: true },
      },
    ]);
    expect(screen.getByRole("link", { name: "查看版本与部署日志" })).toHaveAttribute(
      "href",
      "/ui/account-pool?tab=releases",
    );
  });

  it("blocks enabling automatic deployment without configured database backups", async () => {
    state = { ...state, database_backups_enabled: false };
    show();
    const toggle = await screen.findByRole("switch", { name: "自动部署新镜像" });
    expect(toggle).toHaveAttribute("aria-disabled", "true");
    await userEvent.setup().click(toggle);
    expect(toggle).not.toBeChecked();
    expect(screen.getByText(/请先配置数据库备份/)).toBeInTheDocument();
    expect(requests).toHaveLength(0);
  });

  it.each(["4", "1441", "5.5", ""])("rejects invalid interval %s", async (value) => {
    show();
    fireEvent.change(await screen.findByRole("spinbutton", { name: "检查间隔（分钟）" }), { target: { value } });
    expect(screen.getByRole("button", { name: "保存自动更新设置" })).toBeDisabled();
    expect(screen.getByText("检查间隔须为 5 到 1440 的整数")).toBeInTheDocument();
    expect(requests).toHaveLength(0);
  });

  it("disables an enabled schedule without acknowledgement even when backups become unavailable", async () => {
    state = { ...state, enabled: true, database_backups_enabled: false, status: "paused" };
    const user = userEvent.setup();
    show();
    await user.click(await screen.findByRole("switch", { name: "自动部署新镜像" }));
    await user.click(screen.getByRole("button", { name: "保存自动更新设置" }));
    expect(await screen.findByText("自动更新设置已保存")).toBeInTheDocument();
    expect(requests[0].body).toEqual({ enabled: false, interval_minutes: 5, revision: 2, acknowledge_downtime: false });
    expect(screen.getByRole("switch", { name: "自动部署新镜像" })).not.toBeChecked();
  });

  it("preserves unsaved values and their revision when the background status changes", async () => {
    const { client } = show();
    fireEvent.change(await screen.findByRole("spinbutton", { name: "检查间隔（分钟）" }), { target: { value: "60" } });
    state = { ...state, interval_minutes: 15, revision: 3, message: "发现可用镜像" };
    await act(async () => {
      await client.invalidateQueries();
    });
    expect(await screen.findByText("发现可用镜像")).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "检查间隔（分钟）" })).toHaveValue(60);
    await userEvent.setup().click(screen.getByRole("button", { name: "保存自动更新设置" }));
    expect(await screen.findByText("自动更新设置已保存")).toBeInTheDocument();
    expect(requests[0].body).toEqual({
      enabled: false,
      interval_minutes: 60,
      revision: 2,
      acknowledge_downtime: false,
    });
  });

  it("keeps edits on conflict and lets the user reload the current server settings", async () => {
    show();
    fireEvent.change(await screen.findByRole("spinbutton", { name: "检查间隔（分钟）" }), { target: { value: "60" } });
    state = { ...state, interval_minutes: 15, revision: 3 };
    failure = 409;
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "保存自动更新设置" }));
    expect(await screen.findByText(/保存未完成，编辑内容已保留/)).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "检查间隔（分钟）" })).toHaveValue(60);
    await user.click(screen.getByRole("button", { name: "重新载入设置" }));
    await waitFor(() => expect(screen.getByRole("spinbutton", { name: "检查间隔（分钟）" })).toHaveValue(15));
    failure = 0;
    fireEvent.change(screen.getByRole("spinbutton", { name: "检查间隔（分钟）" }), { target: { value: "30" } });
    await user.click(screen.getByRole("button", { name: "保存自动更新设置" }));
    expect(await screen.findByText("自动更新设置已保存")).toBeInTheDocument();
    expect(requests[1].body).toEqual({
      enabled: false,
      interval_minutes: 30,
      revision: 3,
      acknowledge_downtime: false,
    });
  });

  it("requests a bodyless immediate check while disabled and shows errors without losing edits", async () => {
    const user = userEvent.setup();
    show();
    await user.click(await screen.findByRole("button", { name: "立即检查" }));
    expect(await screen.findByText("检查请求已提交，结果将自动刷新")).toBeInTheDocument();
    expect(requests[0]).toEqual({ path: `${endpoint}/check`, body: undefined, authorization: "Bearer admin-token" });
    expect(screen.getByRole("switch", { name: "自动部署新镜像" })).not.toBeChecked();
    fireEvent.change(screen.getByRole("spinbutton", { name: "检查间隔（分钟）" }), { target: { value: "30" } });
    failure = 503;
    await user.click(screen.getByRole("button", { name: "保存自动更新设置" }));
    expect(await screen.findByText("部署服务不可用")).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "检查间隔（分钟）" })).toHaveValue(30);
  });

  it("explains an old deployment worker's missing endpoint without hiding other settings", async () => {
    getFailure = 404;
    show();
    expect(await screen.findByText(/自动更新接口不可用（404）/)).toHaveTextContent(
      "请确认部署服务已启用并升级到支持自动更新的版本",
    );
    expect(await screen.findByText("配置版本 1")).toBeInTheDocument();
    expect(screen.queryByRole("switch", { name: "自动部署新镜像" })).not.toBeInTheDocument();
    getFailure = 0;
    await userEvent.setup().click(screen.getByRole("button", { name: "重新载入设置" }));
    expect(await screen.findByRole("switch", { name: "自动部署新镜像" })).not.toBeChecked();
  });

  it("disables conflicting actions while a save is pending", async () => {
    show();
    fireEvent.change(await screen.findByRole("spinbutton", { name: "检查间隔（分钟）" }), { target: { value: "30" } });
    let respond!: (response: Response) => void;
    const response = new Promise<Response>((resolve) => {
      respond = resolve;
    });
    vi.mocked(fetch).mockImplementationOnce(() => response);
    await userEvent.setup().click(screen.getByRole("button", { name: "保存自动更新设置" }));
    expect(screen.getByRole("button", { name: "正在保存…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "立即检查" })).toBeDisabled();
    expect(screen.getByRole("spinbutton", { name: "检查间隔（分钟）" })).toBeDisabled();
    expect(screen.getByRole("switch", { name: "自动部署新镜像" })).toHaveAttribute("aria-disabled", "true");
    respond(json({ ...state, interval_minutes: 30, revision: 3 }));
    expect(await screen.findByText("自动更新设置已保存")).toBeInTheDocument();
  });

  it("shows a failed immediate check and supports retrying it", async () => {
    failure = 503;
    show();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "立即检查" }));
    expect(await screen.findByText("部署服务不可用")).toBeInTheDocument();
    expect(screen.queryByText("检查请求已提交，结果将自动刷新")).not.toBeInTheDocument();
    failure = 0;
    await user.click(screen.getByRole("button", { name: "立即检查" }));
    expect(await screen.findByText("检查请求已提交，结果将自动刷新")).toBeInTheDocument();
  });
});
