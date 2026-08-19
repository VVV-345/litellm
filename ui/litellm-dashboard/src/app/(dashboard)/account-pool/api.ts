// 本文件封装 Account Pool Dashboard 通过 LiteLLM 同域管理代理发起的请求。

import { apiClient } from "@/components/networking";

import type {
  ChannelDetail,
  ChannelListResponse,
  ChannelMutationRequest,
  ChannelOperation,
  DeleteMode,
  EffectiveParserData,
  JsonValue,
  OverrideRevokeRequest,
  OverrideSetRequest,
  ParserRunHistory,
  ParserSnapshotDocument,
  ParserTaskAccepted,
  ParserTaskRequest,
  ParserTaskView,
  ProviderServiceManifest,
} from "./types";

export const accountPoolKeys = {
  all: ["account-pool"] as const,
  channels: () => ["account-pool", "channels"] as const,
  channel: (channelId: string) => ["account-pool", "channels", channelId] as const,
  operation: (operationId: string) => ["account-pool", "operations", operationId] as const,
  effective: (channelId: string) => ["account-pool", "channels", channelId, "effective"] as const,
  history: (channelId: string) => ["account-pool", "channels", channelId, "history"] as const,
  providers: () => ["account-pool", "providers"] as const,
  snapshot: (channelId: string) => ["account-pool", "channels", channelId, "snapshot"] as const,
  task: (channelId: string, taskId: string) => ["account-pool", "channels", channelId, "tasks", taskId] as const,
};

export const getChannels = (accessToken: string): Promise<ChannelListResponse> =>
  apiClient.get("/account_pool/channels", { accessToken });

const mutationOptions = (accessToken: string, body: unknown) => ({
  accessToken,
  body,
  headers: { "Idempotency-Key": crypto.randomUUID() },
});

export const getChannel = (accessToken: string, channelId: string): Promise<ChannelDetail> =>
  apiClient.get(`/account_pool/channels/${channelId}`, { accessToken });

export const createChannel = (accessToken: string, request: ChannelMutationRequest): Promise<ChannelOperation> =>
  apiClient.post("/account_pool/channels", mutationOptions(accessToken, request));

export const updateChannel = (
  accessToken: string,
  channelId: string,
  request: ChannelMutationRequest,
): Promise<ChannelOperation> => apiClient.put(`/account_pool/channels/${channelId}`, mutationOptions(accessToken, request));

export const importChannel = (accessToken: string, request: ChannelMutationRequest): Promise<ChannelOperation> =>
  apiClient.post("/account_pool/channels/import", mutationOptions(accessToken, request));

export const detachChannel = (accessToken: string, channelId: string): Promise<ChannelOperation> =>
  apiClient.post(`/account_pool/channels/${channelId}/detach`, mutationOptions(accessToken, {}));

export const deleteChannel = (
  accessToken: string,
  channelId: string,
  deleteMode: DeleteMode,
): Promise<ChannelOperation> =>
  apiClient.delete(
    `/account_pool/channels/${channelId}`,
    mutationOptions(accessToken, { delete_mode: deleteMode }),
  );

export const deleteExternalDeployment = (
  accessToken: string,
  channelId: string,
  bindingId: string,
): Promise<ChannelOperation> =>
  apiClient.post(
    `/account_pool/channels/${channelId}/bindings/${bindingId}/delete-external-deployment`,
    mutationOptions(accessToken, { confirmed: true }),
  );

export const reconcileChannel = (
  accessToken: string,
  channelId: string,
  apiKey: string | null,
): Promise<ChannelOperation> =>
  apiClient.post(`/account_pool/channels/${channelId}/reconcile`, mutationOptions(accessToken, { api_key: apiKey }));

export const getOperation = (accessToken: string, operationId: string): Promise<ChannelOperation> =>
  apiClient.get(`/account_pool/operations/${operationId}`, { accessToken });

export const getEffectiveData = (accessToken: string, channelId: string): Promise<EffectiveParserData> =>
  apiClient.get(`/account_pool/channels/${channelId}/effective-data`, { accessToken });

export const getParserHistory = (accessToken: string, channelId: string): Promise<ParserRunHistory> =>
  apiClient.get(`/account_pool/channels/${channelId}/parser-runs`, { accessToken, query: { limit: 25 } });

export const getProviderServices = (accessToken: string): Promise<ProviderServiceManifest[]> =>
  apiClient.get("/account_pool/provider-services", { accessToken });

export const startParserTask = (
  accessToken: string,
  channelId: string,
  request: ParserTaskRequest,
): Promise<ParserTaskAccepted> =>
  apiClient.post(`/account_pool/channels/${channelId}/parse`, { accessToken, body: request });

export const getParserTask = (accessToken: string, channelId: string, taskId: string): Promise<ParserTaskView> =>
  apiClient.get(`/account_pool/channels/${channelId}/parser-tasks/${taskId}`, { accessToken });

export const getParserSnapshot = (accessToken: string, channelId: string): Promise<ParserSnapshotDocument> =>
  apiClient.get(`/account_pool/channels/${channelId}/snapshot`, { accessToken });

export const importParserSnapshot = (
  accessToken: string,
  channelId: string,
  document: ParserSnapshotDocument,
  reason: string,
): Promise<JsonValue> =>
  apiClient.post(`/account_pool/channels/${channelId}/import`, {
    accessToken,
    body: { import_id: crypto.randomUUID(), reason, document },
  });

export const setParserOverride = (
  accessToken: string,
  channelId: string,
  request: OverrideSetRequest,
): Promise<JsonValue> => apiClient.put(`/account_pool/channels/${channelId}/overrides`, { accessToken, body: request });

export const revokeParserOverride = (
  accessToken: string,
  channelId: string,
  fieldPath: string,
  request: OverrideRevokeRequest,
): Promise<JsonValue> =>
  apiClient.delete(`/account_pool/channels/${channelId}/overrides/${fieldPath.replace(/^\/+/, "")}`, {
    accessToken,
    body: request,
  });
