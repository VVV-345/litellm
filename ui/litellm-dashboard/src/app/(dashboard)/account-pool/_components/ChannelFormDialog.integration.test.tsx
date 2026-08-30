import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { createChannel, discoverUpstreamModels, getChannel, updateChannel } from "../api";
import type { ChannelDetail, ChannelSummary, UpstreamModelDiscoveryResult, UpstreamProviderManifest } from "../types";
import ChannelFormDialog from "./ChannelFormDialog";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    createChannel: vi.fn(),
    discoverUpstreamModels: vi.fn(),
    getChannel: vi.fn(),
    updateChannel: vi.fn(),
  };
});

const openAi: UpstreamProviderManifest = {
  provider_id: "openai",
  display_name: "OpenAI",
  default_api_base: "https://api.openai.com/v1",
};
const gemini: UpstreamProviderManifest = {
  provider_id: "gemini",
  display_name: "Google Gemini",
  default_api_base: "https://generativelanguage.googleapis.com/v1beta",
};
const discovery: UpstreamModelDiscoveryResult = {
  ok: true,
  provider_id: "openai",
  normalized_api_base: openAi.default_api_base,
  message: "已获取 2 个资源侧模型",
  failure_code: null,
  models: ["gpt-5.6", "vendor/model-a"],
};
const summary: ChannelSummary = {
  channel_id: "10000000-0000-0000-0000-000000000001",
  display_name: "摘要名称",
  provider: "openai",
  model_discovery_provider_id: "openai",
  group: null,
  base_url_display: openAi.default_api_base,
  administrative_state: "enabled",
  max_concurrency: 1,
  priority: 200,
  weight: 1,
  key_mask: "sk-***main",
  binding_count: 1,
  enabled_binding_count: 1,
  models: ["gpt-5.6"],
  created_at: "2026-08-19T00:00:00Z",
  updated_at: "2026-08-19T00:00:00Z",
};
const detail: ChannelDetail = {
  channel_id: summary.channel_id,
  display_name: "数据库完整名称",
  provider: "openai",
  model_discovery_provider_id: "openai",
  group: "paid",
  base_url_display: openAi.default_api_base,
  administrative_state: "paused",
  max_concurrency: 8,
  priority: 300,
  weight: 20,
  quotas: { unit: "tokens", total: 1000, five_hour: 100, weekly: 500 },
  key_mask: "sk-***main",
  bindings: [
    {
      binding_id: "20000000-0000-0000-0000-000000000002",
      public_model: "gpt-5.6",
      provider_model: "openai/gpt-5.6",
      litellm_deployment_id: "deployment-1",
      ownership: "pool_managed",
      enabled: true,
    },
  ],
};

const renderDialog = (props: Partial<ComponentProps<typeof ChannelFormDialog>> = {}) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ChannelFormDialog
        accessToken="proxy-token"
        mode="create"
        channel={null}
        providers={[openAi, gemini]}
        knownModels={[]}
        onClose={vi.fn()}
        onAccepted={vi.fn(async () => undefined)}
        {...props}
      />
    </QueryClientProvider>,
  );
};

describe("ChannelFormDialog", () => {
  it("keeps the forwarding protocol in advanced settings and parser controls out of the form", () => {
    renderDialog();

    expect(screen.getByRole("combobox", { name: "上游厂商" })).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "LiteLLM 转发协议" })).not.toBeInTheDocument();
    expect(screen.queryByText("服务商 / 接口协议")).not.toBeInTheDocument();
    expect(screen.queryByText("解析器 ID（可选）")).not.toBeInTheDocument();
  });

  it("discovers raw upstream model IDs and uses a separately selected forwarding protocol", async () => {
    vi.mocked(discoverUpstreamModels).mockResolvedValue(discovery);
    vi.mocked(createChannel).mockResolvedValue({
      status: "accepted",
      operation_id: "operation-1",
      channel_id: "channel-1",
      operation_status: "pending_create",
      requires_key: false,
      failure: null,
    });
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByLabelText("显示名称"), "OpenAI 主渠道");
    await user.type(screen.getByLabelText("接入凭据"), "sk-once");
    await user.click(screen.getByText("高级设置"));
    await user.click(screen.getByRole("combobox", { name: "LiteLLM 转发协议" }));
    await user.click(await screen.findByText("OpenRouter"));
    await user.click(screen.getByRole("button", { name: "从资源侧获取" }));

    expect(await screen.findByText("已获取 2 个模型，默认全部选中")).toBeInTheDocument();
    expect(discoverUpstreamModels).toHaveBeenCalledWith("proxy-token", {
      provider_id: "openai",
      upstream_url: "https://api.openai.com/v1",
      api_key: "sk-once",
    });
    await user.click(screen.getByRole("button", { name: "创建" }));

    expect(createChannel).toHaveBeenCalledWith(
      "proxy-token",
      expect.objectContaining({
        provider: "openrouter",
        model_discovery_provider_id: "openai",
        bindings: [
          expect.objectContaining({ public_model: "gpt-5.6", provider_model: "openrouter/gpt-5.6" }),
          expect.objectContaining({ public_model: "vendor/model-a", provider_model: "openrouter/vendor/model-a" }),
        ],
      }),
    );
  });

  it("changes the request protocol and URL with the selected vendor", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("combobox", { name: "上游厂商" }));
    await user.click(await screen.findByText("Google Gemini"));

    expect(screen.getByLabelText("API 地址")).toHaveValue(gemini.default_api_base);
  });

  it("restores the saved model-discovery vendor when editing a channel", async () => {
    vi.mocked(getChannel).mockResolvedValue(detail);
    renderDialog({ mode: "edit", channel: summary });

    await screen.findByDisplayValue("数据库完整名称");

    expect(screen.getByRole("combobox", { name: "上游厂商" })).toHaveTextContent("OpenAI");
  });

  it("preserves deployment IDs while refreshing an existing channel's models", async () => {
    vi.mocked(getChannel).mockResolvedValue(detail);
    vi.mocked(discoverUpstreamModels).mockResolvedValue({
      ...discovery,
      models: ["gpt-5.6", "gpt-5.7"],
    });
    vi.mocked(updateChannel).mockResolvedValue({
      status: "accepted",
      operation_id: "operation-2",
      channel_id: summary.channel_id,
      operation_status: "pending_update",
      requires_key: false,
      failure: null,
    });
    const user = userEvent.setup();
    renderDialog({ mode: "edit", channel: summary });

    await screen.findByDisplayValue("数据库完整名称");
    await user.type(screen.getByLabelText("用于获取模型的新 Key（可选）"), "sk-replacement");
    await user.click(screen.getByRole("button", { name: "从资源侧获取" }));
    await screen.findByText("已获取 2 个模型，默认全部选中");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(updateChannel).toHaveBeenCalledWith(
      "proxy-token",
      summary.channel_id,
      expect.objectContaining({
        bindings: [
          expect.objectContaining({
            binding_id: detail.bindings[0].binding_id,
            public_model: "gpt-5.6",
            provider_model: "openai/gpt-5.6",
            litellm_deployment_id: "deployment-1",
          }),
          expect.objectContaining({ public_model: "gpt-5.7", provider_model: "openai/gpt-5.7" }),
        ],
      }),
    );
  });

  it("requires an explicit manual fallback after a failed model-list request", async () => {
    vi.mocked(discoverUpstreamModels).mockResolvedValue({
      ...discovery,
      ok: false,
      message: "API Key 无效",
      failure_code: "authentication",
      models: [],
    });
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByLabelText("接入凭据"), "bad-key");
    await user.click(screen.getByRole("button", { name: "从资源侧获取" }));

    expect(await screen.findByText("API Key 无效")).toBeInTheDocument();
    expect(screen.queryByText("公共模型")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "使用手动映射" }));
    expect(screen.getByText("公共模型")).toBeInTheDocument();
  });
});
