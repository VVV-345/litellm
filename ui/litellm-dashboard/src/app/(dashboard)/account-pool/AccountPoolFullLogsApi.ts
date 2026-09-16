/** 本文件封装独立完整日志的查询和清理，正文不写入持久化浏览器缓存。 */
import { apiClient } from "@/components/networking";
import type { components } from "@/lib/http/schema";

export type FullLogSummary = components["schemas"]["FullLogSummary"];
export type FullLogRecord = components["schemas"]["FullLogRecord"];
export type FullLogFilters = { card_id?: string; request_id?: string; session_id?: string; offset?: number };

export const listFullLogs = (accessToken: string, query: FullLogFilters) =>
  apiClient.get<components["schemas"]["FullLogPage"]>("/account_pool/full-logs", { accessToken, query });
export const getFullLog = (accessToken: string, id: string) =>
  apiClient.get<FullLogRecord>(`/account_pool/full-logs/${encodeURIComponent(id)}`, { accessToken });
export const fullLogStorage = (accessToken: string) =>
  apiClient.get<components["schemas"]["FullLogStorageStats"]>("/account_pool/full-logs/storage", { accessToken });
export const clearFullLogs = (accessToken: string, days: number | null) =>
  apiClient.delete<{ deleted: number }>("/account_pool/full-logs", {
    accessToken,
    query: days == null ? {} : { older_than_days: days },
  });
