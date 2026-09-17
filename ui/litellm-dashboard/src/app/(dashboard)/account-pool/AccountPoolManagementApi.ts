/** 本文件封装卡片 Key、管理策略和日志请求，不在查询缓存中保存 Key 明文。 */

import { apiClient } from "@/components/networking";
import type { components } from "@/lib/http/schema";

export type CardKeyStatus = components["schemas"]["CardKeyStatus"];
export type CardKeyIssue = components["schemas"]["CardKeyIssue"];
export type PolicyView = components["schemas"]["PolicyView"];
export type AccountPolicy = components["schemas"]["AccountPolicy"];
export type LogEvent = components["schemas"]["ErrorLogRecord"];
export type LogPage = components["schemas"]["ErrorLogPage"];
export type LogDetail = components["schemas"]["ErrorLogDetail"];
export type ErrorStats = components["schemas"]["ErrorStats"] & {
  cache_read_input_tokens?: number;
  cache_creation_input_tokens?: number;
  cache_rate?: number | null;
};
export type AccountPoolSettings = components["schemas"]["AccountPoolSettings"];
export type AccountPoolSettingsView = components["schemas"]["AccountPoolSettingsView"];
export type AccountPoolSettingsUpdate = components["schemas"]["AccountPoolSettingsUpdate"];
export type AccountPoolSettingsHistoryEntry = components["schemas"]["AccountPoolSettingsHistoryEntry"];
export type AccountPoolSettingsPreview = components["schemas"]["AccountPoolSettingsPreview"];
export type AccessSettingsValues = components["schemas"]["AccessSettingsValues"];
export type AdvancedSettingsValues = components["schemas"]["AdvancedSettingsValues"];
export type CommonSettingsValues = components["schemas"]["CommonSettingsValues"];
export type NetworkSettingsValues = components["schemas"]["NetworkSettingsValues"];
export type PayloadSettings = components["schemas"]["PayloadSettings"];
export type QuotaSettingsValues = components["schemas"]["QuotaSettingsValues"];
export type StreamingSettingsValues = components["schemas"]["StreamingSettingsValues"];
export type AccountPoolCredential = components["schemas"]["AccountPoolCredential"];
export type AccountPoolQuotaRefreshResult = components["schemas"]["AccountPoolQuotaRefreshResult"];
export type AccountPoolLogClearResult = components["schemas"]["AccountPoolLogClearResult"];
export type AccountPoolLogStorageStats = {
  backend: "postgresql";
  location: string;
  row_count: number;
  allocated_bytes: number;
};
export type AccountPoolQuotaRefreshStatus = {
  interval_minutes: 5 | 15 | 30 | 60;
  running: boolean;
  last_started_at: string | null;
  last_completed_at: string | null;
  next_refresh_at: string | null;
  last_failed_count: number | null;
};
export type AccountPoolAuthFileRefreshStatus = AccountPoolQuotaRefreshStatus;
export type AccountPoolPluginManifest = components["schemas"]["AccountPoolPluginManifest"];
export type AccountPoolPluginRecord = components["schemas"]["AccountPoolPluginRecord"];
export type UpstreamSyncView = components["schemas"]["UpstreamSyncView"];
export type UpstreamSyncDispatch = components["schemas"]["UpstreamSyncDispatch"];
export type CodexReviewPackage = components["schemas"]["CodexReviewPackage"];
export type AccountPoolPluginRuntimeResponse = Record<string, unknown>;
export type AccountPoolCredentialRequest = {
  version: number;
  api_key: string;
  proxy_profile_id?: string | null;
  weight?: number;
};
export type AccountPoolCredentialDeleteRequest = {
  version: number;
  credential_index: number;
};
export interface AccountPoolDashboardStats {
  summary: ErrorStats;
  cards: ErrorStats[];
}
export type BatchJob = components["schemas"]["BatchJob"];
export type BatchAction = components["schemas"]["BatchRequest"]["action"];
export type LogFilters = {
  occurred_from?: string;
  occurred_to?: string;
  channel?: string;
  supplier?: string;
  card_id?: string;
  environment_id?: string;
  account_id?: string;
  card_key_id?: string;
  request_id?: string;
  session_id?: string;
  final_status?: string;
  http_status?: number;
  endpoint?: string;
  model?: string;
  stage?: string;
  error_category?: string;
  retryable?: boolean;
  switched_account?: boolean;
  limit?: number;
  offset?: number;
};

export const createAccountPoolJobId = (): string => {
  const randomBytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
  const bytes = Uint8Array.from(randomBytes, (value, index) => {
    if (index === 6) return (value & 0x0f) | 0x40;
    if (index === 8) return (value & 0x3f) | 0x80;
    return value;
  });
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};

const cardPath = (cardId: string) => `/account_pool/cards/${encodeURIComponent(cardId)}/key`;
const policyPath = (cardId: string) => `/account_pool/environments/${encodeURIComponent(cardId)}/policy`;

export const getCardKeyStatus = (accessToken: string, cardId: string) =>
  apiClient.get<CardKeyStatus | null>(`${cardPath(cardId)}/status`, { accessToken });

export const issueCardKey = (accessToken: string, cardId: string, expectedKeyId?: string) =>
  apiClient.post<CardKeyIssue>(`${cardPath(cardId)}${expectedKeyId ? "/rotate" : ""}`, {
    accessToken,
    ...(expectedKeyId ? { body: { expected_key_id: expectedKeyId } } : {}),
  });

export const revokeCardKey = (accessToken: string, cardId: string, expectedKeyId: string) =>
  apiClient.delete<void>(cardPath(cardId), { accessToken, body: { expected_key_id: expectedKeyId } });

export const getAccountPolicy = (accessToken: string, cardId: string) =>
  apiClient.get<PolicyView>(policyPath(cardId), { accessToken });

export const listAccountPolicies = (accessToken: string) =>
  apiClient.get<PolicyView[]>("/account_pool/policies", { accessToken });

export const saveAccountPolicy = (accessToken: string, cardId: string, version: number, policy: AccountPolicy) =>
  apiClient.put<PolicyView>(policyPath(cardId), { accessToken, body: { version, policy } });

export const listAccountPoolLogs = (accessToken: string, query: LogFilters) =>
  apiClient.get<LogPage>("/account_pool/logs", { accessToken, query });

export const getAccountPoolLog = (accessToken: string, eventId: string) =>
  apiClient.get<LogDetail>(`/account_pool/logs/${encodeURIComponent(eventId)}`, { accessToken });

export const getAccountPoolStats = (accessToken: string, query: LogFilters = {}) =>
  apiClient.get<ErrorStats>("/account_pool/stats", { accessToken, query });

export const getAccountPoolDashboardStats = (accessToken: string): Promise<AccountPoolDashboardStats> =>
  apiClient.get<AccountPoolDashboardStats>("/account_pool/dashboard", { accessToken });

export const listAccountPoolCredentials = (accessToken: string) =>
  apiClient.get<AccountPoolCredential[]>("/account_pool/auth-files", { accessToken });

export const uploadAccountPoolAuthFile = (accessToken: string, cardId: string, file: File) => {
  const form: FormData = new FormData();
  form.append("card_id", cardId);
  form.append("file", file, file.name);
  return apiClient.post<components["schemas"]["AccountPoolEnvironment"]>("/account_pool/auth-files", {
    accessToken,
    rawBody: form,
  });
};

export const downloadAccountPoolAuthFile = (accessToken: string, cardId: string) =>
  apiClient.requestBlob("GET", `/account_pool/environments/${encodeURIComponent(cardId)}/auth-file/download`, {
    accessToken,
  });

export const deleteAccountPoolAuthFile = (accessToken: string, cardId: string) =>
  apiClient.delete<components["schemas"]["AccountPoolEnvironment"]>(
    `/account_pool/environments/${encodeURIComponent(cardId)}/auth-file`,
    { accessToken },
  );

export const patchAccountPoolAuthFileStatus = (accessToken: string, cardId: string, disabled: boolean) =>
  apiClient.patch<components["schemas"]["AccountPoolEnvironment"]>(
    `/account_pool/environments/${encodeURIComponent(cardId)}/auth-file/status`,
    { accessToken, body: { disabled } },
  );

export const patchAccountPoolAuthFileFields = (accessToken: string, cardId: string, fields: Record<string, unknown>) =>
  apiClient.patch<components["schemas"]["AccountPoolEnvironment"]>(
    `/account_pool/environments/${encodeURIComponent(cardId)}/auth-file/fields`,
    { accessToken, body: { fields } },
  );

export const getAccountPoolAuthFileModels = (accessToken: string, cardId: string) =>
  apiClient.get<string[]>(`/account_pool/environments/${encodeURIComponent(cardId)}/auth-file/models`, { accessToken });

export const cancelAccountPoolOAuthSession = (accessToken: string, cardId: string) =>
  apiClient.delete<components["schemas"]["AccountPoolEnvironment"]>(
    `/account_pool/environments/${encodeURIComponent(cardId)}/oauth-session`,
    { accessToken },
  );

export const addAccountPoolCredential = (accessToken: string, cardId: string, request: AccountPoolCredentialRequest) =>
  apiClient.post<unknown>(`/account_pool/environments/${encodeURIComponent(cardId)}/credentials`, {
    accessToken,
    body: request,
  });

export const deleteAccountPoolCredential = (
  accessToken: string,
  cardId: string,
  request: AccountPoolCredentialDeleteRequest,
) =>
  apiClient.delete<unknown>(`/account_pool/environments/${encodeURIComponent(cardId)}/credentials`, {
    accessToken,
    body: request,
  });

export const refreshAccountPoolAuthFiles = (accessToken: string) =>
  apiClient.post<AccountPoolAuthFileRefreshStatus>("/account_pool/auth-files/refresh", { accessToken });

export const getAccountPoolAuthFileRefreshStatus = (accessToken: string) =>
  apiClient.get<AccountPoolAuthFileRefreshStatus>("/account_pool/auth-files/refresh/status", { accessToken });

export const setAccountPoolAuthFileRefreshInterval = (
  accessToken: string,
  intervalMinutes: AccountPoolAuthFileRefreshStatus["interval_minutes"],
) =>
  apiClient.put<AccountPoolAuthFileRefreshStatus>("/account_pool/auth-files/refresh/interval", {
    accessToken,
    body: { interval_minutes: intervalMinutes },
  });

export const refreshAccountPoolQuotas = (accessToken: string) =>
  apiClient.post<AccountPoolQuotaRefreshResult>("/account_pool/quotas/refresh", { accessToken });

export const getAccountPoolQuotaRefreshStatus = (accessToken: string) =>
  apiClient.get<AccountPoolQuotaRefreshStatus>("/account_pool/quotas/refresh/status", { accessToken });

export const setAccountPoolQuotaRefreshInterval = (
  accessToken: string,
  intervalMinutes: AccountPoolQuotaRefreshStatus["interval_minutes"],
) =>
  apiClient.put<AccountPoolQuotaRefreshStatus>("/account_pool/quotas/refresh/interval", {
    accessToken,
    body: { interval_minutes: intervalMinutes },
  });

export const clearAccountPoolLogs = (accessToken: string, olderThanDays?: 7 | 14 | 30 | 45) =>
  apiClient.delete<AccountPoolLogClearResult>("/account_pool/logs", {
    accessToken,
    query: olderThanDays ? { older_than_days: olderThanDays } : undefined,
  });

export const getAccountPoolLogStorage = (accessToken: string) =>
  apiClient.get<AccountPoolLogStorageStats>("/account_pool/logs/storage", { accessToken });

export const listAccountPoolPlugins = (accessToken: string) =>
  apiClient.get<AccountPoolPluginRecord[]>("/account_pool/plugins", { accessToken });

export const listAccountPoolPluginStore = (accessToken: string) =>
  apiClient.get<AccountPoolPluginManifest[]>("/account_pool/plugin-store", { accessToken });

export const installAccountPoolPlugin = (accessToken: string, manifest: AccountPoolPluginManifest) =>
  apiClient.post<AccountPoolPluginRecord>("/account_pool/plugins", { accessToken, body: manifest });

export const setAccountPoolPluginEnabled = (accessToken: string, pluginId: string, enabled: boolean) =>
  apiClient.post<AccountPoolPluginRecord>(
    `/account_pool/plugins/${encodeURIComponent(pluginId)}/${enabled ? "enable" : "disable"}`,
    { accessToken },
  );

export const uninstallAccountPoolPlugin = (accessToken: string, pluginId: string) =>
  apiClient.delete<void>(`/account_pool/plugins/${encodeURIComponent(pluginId)}`, { accessToken });

export const listCardAccountPoolPlugins = (accessToken: string, cardId: string) =>
  apiClient.get<AccountPoolPluginRuntimeResponse>(`/account_pool/environments/${encodeURIComponent(cardId)}/plugins`, {
    accessToken,
  });

export const listCardAccountPoolPluginStore = (accessToken: string, cardId: string) =>
  apiClient.get<AccountPoolPluginRuntimeResponse>(
    `/account_pool/environments/${encodeURIComponent(cardId)}/plugin-store`,
    { accessToken },
  );

export const installCardAccountPoolPlugin = (
  accessToken: string,
  cardId: string,
  pluginId: string,
  request: { version: string; source?: string },
) =>
  apiClient.post<AccountPoolPluginRuntimeResponse>(
    `/account_pool/environments/${encodeURIComponent(cardId)}/plugins/${encodeURIComponent(pluginId)}/install`,
    { accessToken, body: request },
  );

export const setCardAccountPoolPluginEnabled = (
  accessToken: string,
  cardId: string,
  pluginId: string,
  enabled: boolean,
) =>
  apiClient.patch<AccountPoolPluginRuntimeResponse>(
    `/account_pool/environments/${encodeURIComponent(cardId)}/plugins/${encodeURIComponent(pluginId)}/enabled`,
    { accessToken, body: { enabled } },
  );

export const uninstallCardAccountPoolPlugin = (accessToken: string, cardId: string, pluginId: string) =>
  apiClient.delete<AccountPoolPluginRuntimeResponse>(
    `/account_pool/environments/${encodeURIComponent(cardId)}/plugins/${encodeURIComponent(pluginId)}`,
    { accessToken },
  );

export const getCardAccountPoolPluginConfig = (accessToken: string, cardId: string, pluginId: string) =>
  apiClient.get<AccountPoolPluginRuntimeResponse>(
    `/account_pool/environments/${encodeURIComponent(cardId)}/plugins/${encodeURIComponent(pluginId)}/config`,
    { accessToken },
  );

export const putCardAccountPoolPluginConfig = (
  accessToken: string,
  cardId: string,
  pluginId: string,
  config: Record<string, unknown>,
) =>
  apiClient.put<AccountPoolPluginRuntimeResponse>(
    `/account_pool/environments/${encodeURIComponent(cardId)}/plugins/${encodeURIComponent(pluginId)}/config`,
    { accessToken, body: config },
  );

export const exportAccountPoolLogs = async (accessToken: string, query: LogFilters): Promise<Blob> => {
  return apiClient.requestBlob("GET", "/account_pool/logs/export", { accessToken, query });
};

export const getAccountPoolSettings = (accessToken: string) =>
  apiClient.get<AccountPoolSettingsView>("/account_pool/settings", { accessToken });

export const listAccountPoolSettingsHistory = (accessToken: string) =>
  apiClient.get<AccountPoolSettingsHistoryEntry[]>("/account_pool/settings/history", { accessToken });

export const updateAccountPoolSettings = (accessToken: string, request: AccountPoolSettingsUpdate) =>
  apiClient.put<AccountPoolSettingsView>("/account_pool/settings", { accessToken, body: request });

export const previewAccountPoolSettings = (accessToken: string, request: AccountPoolSettingsUpdate) =>
  apiClient.post<AccountPoolSettingsPreview>("/account_pool/settings/preview", { accessToken, body: request });

export const rollbackAccountPoolSettings = (accessToken: string, expectedVersion: number, targetVersion: number) =>
  apiClient.post<AccountPoolSettingsView>("/account_pool/settings/rollback", {
    accessToken,
    body: { expected_version: expectedVersion, target_version: targetVersion },
  });

export const listAccountPoolBatches = (accessToken: string) =>
  apiClient.get<BatchJob[]>("/account_pool/batches", { accessToken });

export const submitAccountPoolBatch = (
  accessToken: string,
  action: BatchAction,
  targets: Array<{ account_id: string; version: number; policy_version: number }>,
  policy: AccountPolicy | null = null,
) =>
  apiClient.post<BatchJob>("/account_pool/batches", {
    accessToken,
    body: { job_id: createAccountPoolJobId(), action, targets, policy },
  });

export const getAccountPoolUpstreamSync = (accessToken: string) =>
  apiClient.get<UpstreamSyncView>("/account_pool/upstream-sync", { accessToken });

export const analyzeAccountPoolUpstream = (accessToken: string) =>
  apiClient.post<UpstreamSyncDispatch>("/account_pool/upstream-sync/analyze", { accessToken });

export const promoteAccountPoolUpstream = (accessToken: string) =>
  apiClient.post<UpstreamSyncDispatch>("/account_pool/upstream-sync/promote", { accessToken });

export const getAccountPoolCodexReview = (accessToken: string) =>
  apiClient.get<CodexReviewPackage>("/account_pool/upstream-sync/codex-review", { accessToken });
