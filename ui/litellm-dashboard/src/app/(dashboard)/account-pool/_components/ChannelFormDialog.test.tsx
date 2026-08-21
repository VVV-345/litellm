// 本文件验证编辑渠道时表单等待并使用 PostgreSQL 返回的完整详情。

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { getChannel } from "../api";
import type { ChannelDetail, ChannelSummary } from "../types";
import ChannelFormDialog from "./ChannelFormDialog";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, getChannel: vi.fn() };
});

const summary: ChannelSummary = {
  channel_id: "10000000-0000-0000-0000-000000000001",
  display_name: "摘要名称",
  provider: "openai_compatible",
  group: null,
  base_url_display: "https://summary.example/v1",
  administrative_state: "enabled",
  max_concurrency: 1,
  priority: 200,
  weight: 1,
  key_mask: "sk-***main",
  binding_count: 1,
  enabled_binding_count: 1,
  models: ["summary-model"],
  created_at: "2026-08-19T00:00:00Z",
  updated_at: "2026-08-19T00:00:00Z",
};

const detail: ChannelDetail = {
  channel_id: summary.channel_id,
  display_name: "数据库完整名称",
  provider: "openai_compatible",
  group: "paid",
  base_url_display: "https://database.example/v1",
  administrative_state: "paused",
  max_concurrency: 8,
  priority: 300,
  weight: 20,
  quotas: { unit: "tokens", total: 1000, five_hour: 100, weekly: 500 },
  key_mask: "sk-***main",
  bindings: [
    {
      binding_id: "20000000-0000-0000-0000-000000000002",
      public_model: "database-model",
      provider_model: "openai/database-model",
      litellm_deployment_id: "deployment-1",
      ownership: "pool_managed",
      enabled: true,
    },
  ],
};

describe("ChannelFormDialog", () => {
  it("mounts editable state from the complete channel detail instead of the list summary", async () => {
    vi.mocked(getChannel).mockResolvedValue(detail);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={queryClient}>
        <ChannelFormDialog
          accessToken="proxy-token"
          mode="edit"
          channel={summary}
          providers={[]}
          knownModels={[]}
          onClose={vi.fn()}
          onAccepted={vi.fn(async () => undefined)}
        />
      </QueryClientProvider>,
    );

    expect(await screen.findByDisplayValue("数据库完整名称")).toBeInTheDocument();
    expect(screen.getByDisplayValue("https://database.example/v1")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "优先级" })).toHaveTextContent("高");
    expect(screen.queryByDisplayValue("摘要名称")).not.toBeInTheDocument();
  });
});
