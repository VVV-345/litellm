// 本文件封装 Account Pool Dashboard 通过 LiteLLM 同域管理代理发起的请求。

import { apiClient } from "@/components/networking";

import { generateRequestUuid } from "@/lib/uuid";
import type {
  AccountPoolOverview,
  ChannelAggregateDetail,
  ChannelDetail,
  ChannelHealthDetail,
  ChannelListResponse,
  ChannelMutationRequest,
  ChannelOperation,
  DeleteMode,
  EffectiveParserData,
  EventLogFilters,
  EventLogPage,
  HealthProbeResult,
  JsonValue,
  OverrideRevokeRequest,
  OverrideSetRequest,
  ParserRunHistory,
  ParserSnapshotDocument,
  ParserTaskAccepted,
  ParserTaskRequest,
  ParserTaskView,
  ProviderServiceManifest,
  ProviderValidationRequest,
  ProviderValidationResult,
  RoutingCandidateMutation,
  RoutingModelSummary,
  RoutingPolicyMutation,
  RoutingPolicyState,
  RoutingTableEntry,
} from "./types";

export const accountPoolKeys = {
  all: ["account-pool"] as const,
  overview: () => ["account-pool", "overview"] as const,
  events: (filters: EventLogFilters) => ["account-pool", "events", filters] as const,
  channels: () => ["account-pool", "channels"] as const,
  channel: (channelId: string) => ["account-pool", "channels", channelId] as const,
  aggregate: (channelId: string) => ["account-pool", "channels", channelId, "aggregate"] as const,
  health: (channelId: string) => ["account-pool", "channels", channelId, "health"] as const,
  operation: (operationId: string) => ["account-pool", "operations", operationId] as const,
  effective: (channelId: string) => ["account-pool", "channels", channelId, "effective"] as const,
  history: (channelId: string) => ["account-pool", "channels", channelId, "history"] as const,
  providers: () => ["account-pool", "providers"] as const,
  snapshot: (channelId: string) => ["account-pool", "channels", channelId, "snapshot"] as const,
  task: (channelId: string, taskId: string) => ["account-pool", "channels", channelId, "tasks", taskId] as const,
  routingModels: () => ["account-pool", "routing", "models"] as const,
  routingPolicy: (model: string) => ["account-pool", "routing", model, "policy"] as const,
  routingTable: (model: string) => ["account-pool", "routing", model, "table"] as const,
};

export const getChannels = (accessToken: string): Promise<ChannelListResponse> =>
  apiClient.get("/account_pool/channels", { accessToken });

export const getOverview = (accessToken: string): Promise<AccountPoolOverview> =>
  apiClient.get("/account_pool/overview", { accessToken });

export const getEvents = (accessToken: string, filters: EventLogFilters): Promise<EventLogPage> =>
  apiClient.get("/account_pool/events", { accessToken, query: { ...filters } });

const mutationOptions = (accessToken: string, body: unknown) => ({
  accessToken,
  body,
  headers: { "Idempotency-Key": generateRequestUuid() },
});

export const getChannel = (accessToken: string, channelId: string): Promise<ChannelDetail> =>
  apiClient.get(`/account_pool/channels/${channelId}`, { accessToken });

export const getChannelAggregate = (accessToken: string, channelId: string): Promise<ChannelAggregateDetail> =>
  apiClient.get(`/account_pool/channels/${channelId}/aggregate`, { accessToken });

export const createChannel = (accessToken: string, request: ChannelMutationRequest): Promise<ChannelOperation> =>
  apiClient.post("/account_pool/channels", mutationOptions(accessToken, request));

export const updateChannel = (
  accessToken: string,
  channelId: string,
  request: ChannelMutationRequest,
): Promise<ChannelOperation> =>
  apiClient.put(`/account_pool/channels/${channelId}`, mutationOptions(accessToken, request));

export const importChannel = (accessToken: string, request: ChannelMutationRequest): Promise<ChannelOperation> =>
  apiClient.post("/account_pool/channels/import", mutationOptions(accessToken, request));

export const detachChannel = (accessToken: string, channelId: string): Promise<ChannelOperation> =>
  apiClient.post(`/account_pool/channels/${channelId}/detach`, mutationOptions(accessToken, {}));

export const deleteChannel = (
  accessToken: string,
  channelId: string,
  deleteMode: DeleteMode,
): Promise<ChannelOperation> =>
  apiClient.delete(`/account_pool/channels/${channelId}`, mutationOptions(accessToken, { delete_mode: deleteMode }));

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

export const probeChannelHealth = (accessToken: string, channelId: string): Promise<HealthProbeResult> =>
  apiClient.post(`/account_pool/channels/${channelId}/health-probe`, { accessToken, body: {} });

export const getChannelHealth = (accessToken: string, channelId: string): Promise<ChannelHealthDetail> =>
  apiClient.get(`/account_pool/channels/${channelId}/health`, { accessToken });

export const getOperation = (accessToken: string, operationId: string): Promise<ChannelOperation> =>
  apiClient.get(`/account_pool/operations/${operationId}`, { accessToken });

export const getEffectiveData = (accessToken: string, channelId: string): Promise<EffectiveParserData> =>
  apiClient.get(`/account_pool/channels/${channelId}/effective-data`, { accessToken });

export const getParserHistory = (accessToken: string, channelId: string): Promise<ParserRunHistory> =>
  apiClient.get(`/account_pool/channels/${channelId}/parser-runs`, { accessToken, query: { limit: 25 } });

export const getProviderServices = (accessToken: string): Promise<ProviderServiceManifest[]> =>
  apiClient.get("/account_pool/provider-services", { accessToken });

export const validateProviderService = (
  accessToken: string,
  request: ProviderValidationRequest,
): Promise<ProviderValidationResult> =>
  apiClient.post("/account_pool/provider-services/validate", { accessToken, body: request });

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
    body: { import_id: generateRequestUuid(), reason, document },
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

const routingModelPath = (model: string): string => encodeURIComponent(model);

export const getRoutingModels = (accessToken: string): Promise<RoutingModelSummary[]> =>
  apiClient.get("/account_pool/models", { accessToken });

export const getRoutingTable = (accessToken: string, model: string): Promise<RoutingTableEntry[]> =>
  apiClient.get(`/account_pool/models/${routingModelPath(model)}/routing-table`, { accessToken });

export const getRoutingPolicy = (accessToken: string, model: string): Promise<RoutingPolicyState> =>
  apiClient.get(`/account_pool/models/${routingModelPath(model)}/routing-policy`, { accessToken });

export const updateRoutingPolicy = (
  accessToken: string,
  model: string,
  request: RoutingPolicyMutation,
): Promise<RoutingPolicyState> =>
  apiClient.put(`/account_pool/models/${routingModelPath(model)}/routing-policy`, { accessToken, body: request });

export const updateRoutingCandidate = (
  accessToken: string,
  model: string,
  bindingId: string,
  request: RoutingCandidateMutation,
): Promise<RoutingPolicyState> =>
  apiClient.put(`/account_pool/models/${routingModelPath(model)}/routing-candidates/${bindingId}`, {
    accessToken,
    body: request,
  });

export const resetRoutingCandidate = (
  accessToken: string,
  model: string,
  bindingId: string,
  expectedVersion: number,
): Promise<RoutingPolicyState> =>
  apiClient.delete(`/account_pool/models/${routingModelPath(model)}/routing-candidates/${bindingId}`, {
    accessToken,
    body: { expected_version: expectedVersion },
  });
