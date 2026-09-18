import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AccountPoolSettingsOverview } from "./AccountPoolSettingsOverview";

const getSettings = vi.fn();
vi.mock("./AccountPoolManagementApi", () => ({ getAccountPoolSettings: (...args: unknown[]) => getSettings(...args) }));
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
  it("shows server values, card overrides and direct destinations without editors", async () => {
    renderOverview();
    expect(await screen.findByText(/配置版本 17/)).toHaveTextContent("需重新加载");
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("关闭")).toBeInTheDocument();
    expect(screen.getByText(/Card streaming/)).toHaveTextContent("card-a");
    expect(await screen.findByText("simple-shuffle")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "供应商模型与访问" })).toHaveAttribute(
      "href",
      "/ui/models-and-endpoints?tab=model-group-alias",
    );
    expect(screen.getByRole("link", { name: "流式传输" })).toHaveAttribute("href", "/ui/router-settings?tab=streaming");
    expect(screen.getByRole("link", { name: "日常日志与完整日志" })).toHaveAttribute(
      "href",
      "/ui/logs?log_view=settings",
    );
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByText(/不参与当前跨卡选卡与重试/)).toBeInTheDocument();
  });
  it("reports unavailable settings rather than displaying defaults", async () => {
    getSettings.mockRejectedValue(new Error("unavailable"));
    renderOverview();
    expect(await screen.findByRole("alert")).toHaveTextContent("运行配置读取失败");
    expect(screen.queryByText(/配置版本/)).not.toBeInTheDocument();
  });
});
