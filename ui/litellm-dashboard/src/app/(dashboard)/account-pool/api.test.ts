// 本文件验证渠道生命周期请求的端点、幂等头和凭证载荷边界。

import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiClient } from "@/components/networking";

import {
  createChannel,
  deleteChannel,
  deleteExternalDeployment,
  detachChannel,
  discoverUpstreamModels,
  getChannelHealth,
  getChannelAggregate,
  getEvents,
  getOverview,
  getOperation,
  importChannel,
  importParserSnapshot,
  probeChannelHealth,
  reconcileChannel,
  resetRoutingCandidate,
  updateRoutingCandidate,
  updateRoutingPolicy,
  updateChannel,
  validateProviderService,
} from "./api";
import type { ChannelMutationRequest } from "./types";

vi.mock("@/components/networking", () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

const mockedGet = vi.mocked(apiClient.get);
const mockedPost = vi.mocked(apiClient.post);
const mockedPut = vi.mocked(apiClient.put);
const mockedDelete = vi.mocked(apiClient.delete);

const request: ChannelMutationRequest = {
  display_name: "主渠道",
  provider: "openai_compatible",
  model_discovery_provider_id: "openai_compatible",
  group: null,
  base_url_display: "https://gateway.example.com/v1",
  administrative_state: "enabled",
  max_concurrency: 8,
  priority: 300,
  weight: 20,
  quotas: { unit: "tokens", total: null, five_hour: null, weekly: null },
  api_key: "sk-once",
  bindings: [
    {
      binding_id: null,
      public_model: "gpt-5.6",
      provider_model: "openai/gpt-5.6",
      litellm_deployment_id: null,
      ownership: "pool_managed",
      enabled: true,
    },
  ],
};

describe("channel lifecycle API", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("crypto", { randomUUID: vi.fn(() => "request-id") });
    mockedGet.mockResolvedValue({});
    mockedPost.mockResolvedValue({});
    mockedPut.mockResolvedValue({});
    mockedDelete.mockResolvedValue({});
  });

  it("sends create and update credentials only in request bodies with a fresh idempotency header", async () => {
    await createChannel("token", request);
    await updateChannel("token", "channel-1", { ...request, api_key: null });

    expect(mockedPost).toHaveBeenCalledWith("/account_pool/channels", {
      accessToken: "token",
      body: request,
      headers: { "Idempotency-Key": "request-id" },
    });
    expect(mockedPut).toHaveBeenCalledWith("/account_pool/channels/channel-1", {
      accessToken: "token",
      body: { ...request, api_key: null },
      headers: { "Idempotency-Key": "request-id" },
    });
  });

  it("keeps lifecycle import separate from parser snapshot import", async () => {
    await importChannel("token", request);
    await importParserSnapshot("token", "channel-1", {}, "核对");

    expect(mockedPost).toHaveBeenNthCalledWith(1, "/account_pool/channels/import", {
      accessToken: "token",
      body: request,
      headers: { "Idempotency-Key": "request-id" },
    });
    expect(mockedPost).toHaveBeenNthCalledWith(2, "/account_pool/channels/channel-1/import", {
      accessToken: "token",
      body: { import_id: "request-id", reason: "核对", document: {} },
    });
  });

  it("sends explicit detach, external deletion, deletion, reconcile, and operation requests", async () => {
    await detachChannel("token", "channel-1");
    await deleteExternalDeployment("token", "channel-1", "binding-1");
    await deleteChannel("token", "channel-1", "delete_managed_deployment");
    await reconcileChannel("token", "channel-1", "sk-retry");
    await getOperation("token", "operation-1");

    expect(mockedPost).toHaveBeenNthCalledWith(1, "/account_pool/channels/channel-1/detach", {
      accessToken: "token",
      body: {},
      headers: { "Idempotency-Key": "request-id" },
    });
    expect(mockedPost).toHaveBeenNthCalledWith(
      2,
      "/account_pool/channels/channel-1/bindings/binding-1/delete-external-deployment",
      {
        accessToken: "token",
        body: { confirmed: true },
        headers: { "Idempotency-Key": "request-id" },
      },
    );
    expect(mockedDelete).toHaveBeenCalledWith("/account_pool/channels/channel-1", {
      accessToken: "token",
      body: { delete_mode: "delete_managed_deployment" },
      headers: { "Idempotency-Key": "request-id" },
    });
    expect(mockedPost).toHaveBeenNthCalledWith(3, "/account_pool/channels/channel-1/reconcile", {
      accessToken: "token",
      body: { api_key: "sk-retry" },
      headers: { "Idempotency-Key": "request-id" },
    });
    expect(apiClient.get).toHaveBeenCalledWith("/account_pool/operations/operation-1", { accessToken: "token" });
  });

  it("validates a provider service without lifecycle idempotency headers", async () => {
    const validationRequest = {
      provider_id: "openai_compatible",
      api_base: "https://gateway.example/v1",
      api_key: "sk-once",
      group: null,
    };

    await validateProviderService("token", validationRequest);

    expect(mockedPost).toHaveBeenCalledWith("/account_pool/provider-services/validate", {
      accessToken: "token",
      body: validationRequest,
    });
  });

  it("sends discovery URLs through the dedicated upstream field", async () => {
    const discoveryRequest = {
      provider_id: "openai",
      upstream_url: "https://gateway.example/v1",
      api_key: "sk-once",
    };

    await discoverUpstreamModels("token", discoveryRequest);

    expect(mockedPost).toHaveBeenCalledWith("/account_pool/upstream-providers/discover-models", {
      accessToken: "token",
      body: discoveryRequest,
    });
  });

  it("runs a directed health probe without sending provider credentials", async () => {
    await probeChannelHealth("token", "channel-1");

    expect(mockedPost).toHaveBeenCalledWith("/account_pool/channels/channel-1/health-probe", {
      accessToken: "token",
      body: {},
    });
  });

  it("loads the redacted channel health detail", async () => {
    await getChannelHealth("token", "channel-1");

    expect(mockedGet).toHaveBeenCalledWith("/account_pool/channels/channel-1/health", {
      accessToken: "token",
    });
  });

  it("loads the partitioned aggregate channel detail", async () => {
    await getChannelAggregate("token", "channel-1");

    expect(mockedGet).toHaveBeenCalledWith("/account_pool/channels/channel-1/aggregate", {
      accessToken: "token",
    });
  });

  it("loads the aggregate overview through the LiteLLM management proxy", async () => {
    await getOverview("token");

    expect(mockedGet).toHaveBeenCalledWith("/account_pool/overview", {
      accessToken: "token",
    });
  });

  it("loads filtered event pages through the LiteLLM management proxy", async () => {
    const filters = { channel_id: "channel-1", outcome: "failed" as const, limit: 50 };
    await getEvents("token", filters);

    expect(mockedGet).toHaveBeenCalledWith("/account_pool/events", {
      accessToken: "token",
      query: filters,
    });
  });

  it("uses encoded model paths and versioned routing mutations", async () => {
    await updateRoutingPolicy("token", "openai/gpt-5.6", { expected_version: 2, strategy: "lowest_latency" });
    await updateRoutingCandidate("token", "openai/gpt-5.6", "binding-1", {
      expected_version: 3,
      manual_order: 0,
      weight: 8,
      paused: true,
    });
    await resetRoutingCandidate("token", "openai/gpt-5.6", "binding-1", 4);

    expect(mockedPut).toHaveBeenNthCalledWith(1, "/account_pool/models/openai%2Fgpt-5.6/routing-policy", {
      accessToken: "token",
      body: { expected_version: 2, strategy: "lowest_latency" },
    });
    expect(mockedPut).toHaveBeenNthCalledWith(2, "/account_pool/models/openai%2Fgpt-5.6/routing-candidates/binding-1", {
      accessToken: "token",
      body: { expected_version: 3, manual_order: 0, weight: 8, paused: true },
    });
    expect(mockedDelete).toHaveBeenCalledWith("/account_pool/models/openai%2Fgpt-5.6/routing-candidates/binding-1", {
      accessToken: "token",
      body: { expected_version: 4 },
    });
  });
});
