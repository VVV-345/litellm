import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AccountPoolSettingsOverview } from "./AccountPoolSettingsOverview";

const getSettings = vi.fn();
vi.mock("../../api/AccountPoolManagementApi", () => ({
  getAccountPoolSettings: (...args: unknown[]) => getSettings(...args),
  getAccountPoolCockpitPolicy: async () => ({
    revision: '"sha256-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"',
    routing_strategy: "auto",
    custom_rules: [],
    account_ids: [],
    editable: false,
    blocked_reason: null,
    scope: "codex_pool_global",
    runtime_activation: "restart_required_or_unverified",
    supported_strategies: ["auto"],
    unsupported_fields: [],
  }),
}));
vi.mock("@/components/networking", () => ({
  serverRootPath: "",
  apiClient: {
    get: async (path: string) =>
      path.startsWith("/router/")
        ? {
            fields: [
              { field_name: "routing_strategy", field_value: "simple-shuffle" },
              { field_name: "num_retries", field_value: 0 },
            ],
          }
        : [{ field_name: "max_parallel_requests", field_value: 6 }],
  },
}));
const renderOverview = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AccountPoolSettingsOverview accessToken="token" />
    </QueryClientProvider>,
  );

describe("global settings overview", () => {
  beforeEach(() => {
    getSettings.mockReset();
    getSettings.mockResolvedValue({
      version: 17,
      requires_reload: true,
      values: {
        default_concurrency_limit: 7,
        default_route: "quota",
        request_retry: 1,
        max_retry_credentials: 3,
        max_retry_interval: 15,
        streaming_enabled: false,
        full_logging_enabled: true,
        streaming_profiles: [
          {
            id: "stream-profile",
            name: "Card streaming",
            card_ids: ["card-a"],
            inherit_global: false,
            values: { enabled: true },
          },
        ],
      },
    });
  });
  it("mounts the global Cockpit policy separately from native router settings", async () => {
    renderOverview();
    expect(await screen.findByText("Cockpit Codex 号池全局策略")).toBeInTheDocument();
    expect(screen.getByText(/整个 Codex 号池/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存全局策略" })).toBeDisabled();
  });
  it("shows server values, card overrides and direct destinations without editors", async () => {
    renderOverview();
    expect(await screen.findByText(/配置版本 17/)).toBeInTheDocument();
    expect(screen.getByText(/需重新加载/)).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("关闭")).toBeInTheDocument();
    expect(screen.getByText(/Card streaming/)).toHaveTextContent("card-a");
    expect(await screen.findByText("simple-shuffle")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "供应商模型与访问" })).toHaveAttribute("href", "/ui/policy-center");
    expect(screen.getByRole("link", { name: "流式传输" })).toHaveAttribute("href", "/ui/policy-center");
    expect(screen.getByRole("link", { name: "日常日志与完整日志" })).toHaveAttribute(
      "href",
      "/ui/logs?log_view=settings",
    );
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "保留的旧路由值" })).not.toBeInTheDocument();
  });
  it("uses distinct semantic icons without changing link labels", async () => {
    renderOverview();
    await screen.findByText(/配置版本 17/);
    for (const [name, icon] of [
      ["新账号默认值", "user-plus"],
      ["出站网络", "network"],
      ["认证刷新", "key-round"],
      ["运行插件", "puzzle"],
      ["日常日志与完整日志", "scroll-text"],
    ]) {
      expect(screen.getByRole("link", { name }).querySelector(`.lucide-${icon}`)).toHaveAttribute(
        "aria-hidden",
        "true",
      );
    }
  });
  it("reports unavailable settings rather than displaying defaults", async () => {
    getSettings.mockRejectedValue(new Error("unavailable"));
    renderOverview();
    expect(await screen.findByRole("alert")).toHaveTextContent("运行配置读取失败");
    expect(screen.queryByText(/配置版本/)).not.toBeInTheDocument();
  });
});
