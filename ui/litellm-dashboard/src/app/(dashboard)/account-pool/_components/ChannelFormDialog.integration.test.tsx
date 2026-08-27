import { useState } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createChannel, getChannel, validateProviderService } from "../api";
import type { ChannelDetail, ChannelSummary, ProviderServiceManifest, ProviderValidationResult } from "../types";
import ChannelFormDialog from "./ChannelFormDialog";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, createChannel: vi.fn(), getChannel: vi.fn(), validateProviderService: vi.fn() };
});

const discoveryProvider: ProviderServiceManifest = {
  provider_id: "openai_compatible",
  display_name: "Compatible API",
  default_api_base: "https://gateway.example/v1",
  litellm_provider_prefix: "openai",
  capabilities: [{ capability: "model_discovery", state: "supported", message: "Available" }],
};

const unavailableDiscoveryProvider: ProviderServiceManifest = {
  ...discoveryProvider,
  provider_id: "limited",
  display_name: "Limited API",
  capabilities: [{ capability: "model_discovery", state: "unavailable", message: "Unavailable" }],
};

const discoveryResult: ProviderValidationResult = {
  ok: true,
  provider_id: "openai_compatible",
  normalized_api_base: "https://gateway.example/v1",
  group: null,
  key_fingerprint: null,
  message: "Validated",
  failure_code: null,
  models: [{ model: "vendor/model-a" }, { model: "model-b" }],
};

const failedDiscoveryResult: ProviderValidationResult = {
  ...discoveryResult,
  ok: false,
  message: "The upstream rejected this credential",
  failure_code: "authentication_failed",
  models: [],
};

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

function DialogHarness({ providers = [discoveryProvider] }: { providers?: ProviderServiceManifest[] }) {
  const [open, setOpen] = useState(true);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  return open ? (
    <QueryClientProvider client={queryClient}>
      <ChannelFormDialog
        accessToken="proxy-token"
        mode="create"
        channel={null}
        providers={providers}
        knownModels={[]}
        onClose={() => setOpen(false)}
        onAccepted={vi.fn(async () => undefined)}
      />
    </QueryClientProvider>
  ) : (
    <button type="button" onClick={() => setOpen(true)}>
      Reopen
    </button>
  );
}

describe("ChannelFormDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(createChannel).mockResolvedValue({
      status: "accepted",
      operation_id: "operation-1",
      channel_id: "channel-1",
      operation_status: "pending_create",
      requires_key: false,
      failure: null,
    });
  });

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

  it("uses current provider discovery when manifests arrive after mount", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const props = {
      accessToken: "proxy-token",
      mode: "create" as const,
      channel: null,
      knownModels: [],
      onClose: vi.fn(),
      onAccepted: vi.fn(async () => undefined),
    };
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <ChannelFormDialog {...props} providers={[]} />
      </QueryClientProvider>,
    );

    expect(screen.getByText(/无法使用上游模型发现/)).toBeInTheDocument();

    rerender(
      <QueryClientProvider client={queryClient}>
        <ChannelFormDialog {...props} providers={[discoveryProvider]} />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("button", { name: "验证并发现模型" })).toBeInTheDocument();
  });

  it("retains discovered selections across an equivalent manifest refetch", async () => {
    vi.mocked(validateProviderService).mockResolvedValue(discoveryResult);
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const props = {
      accessToken: "proxy-token",
      mode: "create" as const,
      channel: null,
      knownModels: [],
      onClose: vi.fn(),
      onAccepted: vi.fn(async () => undefined),
    };
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <ChannelFormDialog {...props} providers={[discoveryProvider]} />
      </QueryClientProvider>,
    );

    await user.type(screen.getByLabelText("上游 URL"), "https://gateway.example/v1");
    await user.type(screen.getByLabelText("API Key（可选）"), "sk-once");
    await user.click(screen.getByRole("button", { name: "验证并发现模型" }));
    await user.click(await screen.findByRole("combobox", { name: "选择已验证的上游模型" }));
    await user.click(await screen.findByText("model-b"));
    await user.keyboard("{Escape}");

    rerender(
      <QueryClientProvider client={queryClient}>
        <ChannelFormDialog {...props} providers={[{ ...discoveryProvider }]} />
      </QueryClientProvider>,
    );

    expect(screen.getByLabelText("model-b")).toBeInTheDocument();
  });

  it("requires a successful validation before creating discovery-backed bindings", async () => {
    vi.mocked(validateProviderService).mockResolvedValue(discoveryResult);
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={queryClient}>
        <ChannelFormDialog
          accessToken="proxy-token"
          mode="create"
          channel={null}
          providers={[discoveryProvider]}
          knownModels={["generic-suggestion"]}
          onClose={vi.fn()}
          onAccepted={vi.fn(async () => undefined)}
        />
      </QueryClientProvider>,
    );

    await user.type(screen.getByLabelText("显示名称"), "Primary");
    await user.type(screen.getByLabelText("上游 URL"), "https://gateway.example/v1");
    await user.type(screen.getByLabelText("API Key（可选）"), "sk-once");
    await user.click(screen.getByRole("button", { name: "验证并发现模型" }));

    expect(await screen.findByRole("button", { name: "使用手动映射" })).toBeInTheDocument();
    expect(validateProviderService).toHaveBeenCalledWith("proxy-token", {
      provider_id: "openai_compatible",
      api_base: "https://gateway.example/v1",
      api_key: "sk-once",
      group: null,
    });
    expect(screen.queryByText("generic-suggestion")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建" })).toBeDisabled();

    await user.click(screen.getByRole("combobox", { name: "选择已验证的上游模型" }));
    await user.click(await screen.findByText("vendor/model-a"));
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("combobox", { name: "选择已验证的上游模型" }));
    await user.click(await screen.findByText("model-b"));
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "创建" }));

    expect(createChannel).toHaveBeenCalledWith(
      "proxy-token",
      expect.objectContaining({
        display_name: "Primary",
        bindings: [
          expect.objectContaining({ public_model: "vendor/model-a", provider_model: "vendor/model-a" }),
          expect.objectContaining({ public_model: "model-b", provider_model: "openai/model-b" }),
        ],
      }),
    );
  });

  it("shows the safe validation failure until manual mapping is explicit", async () => {
    vi.mocked(validateProviderService).mockResolvedValue(failedDiscoveryResult);
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={queryClient}>
        <ChannelFormDialog
          accessToken="proxy-token"
          mode="create"
          channel={null}
          providers={[discoveryProvider]}
          knownModels={[]}
          onClose={vi.fn()}
          onAccepted={vi.fn(async () => undefined)}
        />
      </QueryClientProvider>,
    );

    await user.type(screen.getByLabelText("上游 URL"), "https://gateway.example/v1");
    await user.type(screen.getByLabelText("API Key（可选）"), "sk-once");
    await user.click(screen.getByRole("button", { name: "验证并发现模型" }));

    expect(await screen.findByText("The upstream rejected this credential")).toBeInTheDocument();
    expect(screen.getByText("错误代码: authentication_failed")).toBeInTheDocument();
    expect(screen.queryByText("公共模型")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "使用手动映射" }));

    expect(screen.getByText("公共模型")).toBeInTheDocument();
  });

  it("invalidates discovered models when the upstream URL changes", async () => {
    vi.mocked(validateProviderService).mockResolvedValue(discoveryResult);
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={queryClient}>
        <ChannelFormDialog
          accessToken="proxy-token"
          mode="create"
          channel={null}
          providers={[discoveryProvider]}
          knownModels={[]}
          onClose={vi.fn()}
          onAccepted={vi.fn(async () => undefined)}
        />
      </QueryClientProvider>,
    );

    await user.type(screen.getByLabelText("上游 URL"), "https://gateway.example/v1");
    await user.type(screen.getByLabelText("API Key（可选）"), "sk-once");
    await user.click(screen.getByRole("button", { name: "验证并发现模型" }));
    expect(await screen.findByRole("button", { name: "使用手动映射" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("上游 URL"), "/changed");

    expect(screen.getByRole("button", { name: "验证并发现模型" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建" })).toBeDisabled();
  });

  it("invalidates discovered models when provider, API key, or group changes", async () => {
    vi.mocked(validateProviderService).mockResolvedValue(discoveryResult);
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={queryClient}>
        <ChannelFormDialog
          accessToken="proxy-token"
          mode="create"
          channel={null}
          providers={[discoveryProvider, unavailableDiscoveryProvider]}
          knownModels={[]}
          onClose={vi.fn()}
          onAccepted={vi.fn(async () => undefined)}
        />
      </QueryClientProvider>,
    );

    await user.type(screen.getByLabelText("上游 URL"), "https://gateway.example/v1");
    await user.type(screen.getByLabelText("API Key（可选）"), "sk-once");
    await user.click(screen.getByRole("button", { name: "验证并发现模型" }));
    expect(await screen.findByRole("button", { name: "使用手动映射" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("API Key（可选）"), "-changed");
    expect(screen.getByRole("button", { name: "验证并发现模型" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "验证并发现模型" }));
    expect(await screen.findByRole("button", { name: "使用手动映射" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("分组（可选）"), "enterprise");
    expect(screen.getByRole("button", { name: "验证并发现模型" })).toBeInTheDocument();

    await user.click(screen.getAllByRole("combobox")[0]);
    await user.click(await screen.findByText("Limited API"));
    expect(screen.getByText(/无法使用上游模型发现/)).toBeInTheDocument();
  });

  it("preserves manual bindings when discovery inputs change", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={queryClient}>
        <ChannelFormDialog
          accessToken="proxy-token"
          mode="create"
          channel={null}
          providers={[unavailableDiscoveryProvider]}
          knownModels={[]}
          onClose={vi.fn()}
          onAccepted={vi.fn(async () => undefined)}
        />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("button", { name: "使用手动映射" }));
    const publicModelInput = screen.getByLabelText("公共模型");
    const providerModelInput = screen.getByLabelText("Provider 模型");
    await user.type(publicModelInput, "customer-alias");
    await user.type(providerModelInput, "upstream-model");
    await user.type(screen.getByLabelText("上游 URL"), "https://changed.example/v1");
    await user.type(screen.getByLabelText("API Key（可选）"), "sk-changed");
    await user.type(screen.getByLabelText("分组（可选）"), "changed");

    expect(screen.getByDisplayValue("customer-alias")).toBeInTheDocument();
    expect(screen.getByDisplayValue("upstream-model")).toBeInTheDocument();
  });

  it("clears the API key when closed and reopened", async () => {
    const user = userEvent.setup();

    render(<DialogHarness />);

    await user.type(screen.getByLabelText("API Key（可选）"), "sk-once");
    await user.click(screen.getByRole("button", { name: "取消" }));
    await user.click(screen.getByRole("button", { name: "Reopen" }));

    expect(screen.getByLabelText("API Key（可选）")).toHaveValue("");
  });

  it("requires explicit manual fallback without validating unavailable discovery", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={queryClient}>
        <ChannelFormDialog
          accessToken="proxy-token"
          mode="create"
          channel={null}
          providers={[unavailableDiscoveryProvider]}
          knownModels={[]}
          onClose={vi.fn()}
          onAccepted={vi.fn(async () => undefined)}
        />
      </QueryClientProvider>,
    );

    expect(screen.getByText(/无法使用上游模型发现/)).toBeInTheDocument();
    expect(screen.queryByText("公共模型")).not.toBeInTheDocument();
    expect(validateProviderService).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "使用手动映射" }));

    expect(screen.getByText("公共模型")).toBeInTheDocument();
  });
});
