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
export type ErrorStats = components["schemas"]["ErrorStats"];
export type AccountPoolSettings = components["schemas"]["AccountPoolSettings"];
export type AccountPoolSettingsView = components["schemas"]["AccountPoolSettingsView"];
export type AccountPoolSettingsUpdate = components["schemas"]["AccountPoolSettingsUpdate"];
export type AccountPoolSettingsHistoryEntry = components["schemas"]["AccountPoolSettingsHistoryEntry"];
export type AccountPoolSettingsPreview = components["schemas"]["AccountPoolSettingsPreview"];
export type AccountPoolCredential = components["schemas"]["AccountPoolCredential"];
export type AccountPoolQuotaRefreshResult = components["schemas"]["AccountPoolQuotaRefreshResult"];
export type AccountPoolLogClearResult = components["schemas"]["AccountPoolLogClearResult"];
export type AccountPoolPluginManifest = components["schemas"]["AccountPoolPluginManifest"];
export type AccountPoolPluginRecord = components["schemas"]["AccountPoolPluginRecord"];
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
  model?: string;
  stage?: string;
  error_category?: string;
  retryable?: boolean;
  switched_account?: boolean;
  limit?: number;
  offset?: number;
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

export const getAccountPoolStats = (
  accessToken: string,
  query: Pick<LogFilters, "card_id" | "account_id" | "model"> = {},
) => apiClient.get<ErrorStats>("/account_pool/stats", { accessToken, query });

export const getAccountPoolDashboardStats = (accessToken: string): Promise<AccountPoolDashboardStats> =>
  apiClient.get<AccountPoolDashboardStats>("/account_pool/dashboard", { accessToken });

export const listAccountPoolCredentials = (accessToken: string) =>
  apiClient.get<AccountPoolCredential[]>("/account_pool/credentials", { accessToken });

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

export const refreshAccountPoolQuotas = (accessToken: string) =>
  apiClient.post<AccountPoolQuotaRefreshResult>("/account_pool/quotas/refresh", { accessToken });

export const clearAccountPoolLogs = (accessToken: string) =>
  apiClient.delete<AccountPoolLogClearResult>("/account_pool/logs", { accessToken });

export const listAccountPoolPlugins = (accessToken: string) =>
  apiClient.get<AccountPoolPluginRecord[]>("/account_pool/plugins", { accessToken });

export const listAccountPoolPluginStore = (accessToken: string) =>
  apiClient.get<AccountPoolPluginManifest[]>("/account_pool/plugin-store", { accessToken });

export const installAccountPoolPlugin = (accessToken: string, manifest: AccountPoolPluginManifest) =>
  apiClient.post<AccountPoolPluginRecord>("/account_pool/plugins", { accessToken, body: manifest });

export const setAccountPoolPluginEnabled = (accessToken: string, pluginId: string, enabled: boolean) =>
  apiClient.post<AccountPoolPluginRecord>(`/account_pool/plugins/${encodeURIComponent(pluginId)}/${enabled ? "enable" : "disable"}`, { accessToken });

export const uninstallAccountPoolPlugin = (accessToken: string, pluginId: string) =>
  apiClient.delete<void>(`/account_pool/plugins/${encodeURIComponent(pluginId)}`, { accessToken });

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
    body: { job_id: crypto.randomUUID(), action, targets, policy },
  });
