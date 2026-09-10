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
