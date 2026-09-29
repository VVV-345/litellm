/** 本文件封装独立完整日志的查询和清理，正文不写入持久化浏览器缓存。 */
import { apiClient } from "@/components/networking";
import type { components } from "@/lib/http/schema";

export type FullLogSummary = components["schemas"]["FullLogSummary"];
export type FullLogSession = components["schemas"]["FullLogSession"];
export type FullLogRecord = components["schemas"]["FullLogRecord"];
export type FullLogFilters = {
  card_id?: string;
  key_id?: string;
  request_id?: string;
  session_id?: string;
  model?: string;
  http_status?: number;
  incomplete?: boolean;
  occurred_from?: string;
  occurred_to?: string;
  offset?: number;
  limit?: number;
};

export const normalizeFullLogFilters = (filters: FullLogFilters): FullLogFilters => ({
  ...filters,
  offset: filters.offset ?? 0,
  limit: filters.limit ?? 50,
});

export const listFullLogs = (accessToken: string, query: FullLogFilters) =>
  apiClient.get<components["schemas"]["FullLogPage"]>("/logs/full", { accessToken, query, cache: "no-store" });

export const fullLogsQueryOptions = (
  accessToken: string,
  filters: FullLogFilters,
  refreshInterval: number | false = false,
) => {
  const normalizedFilters = normalizeFullLogFilters(filters);
  return {
    queryKey: ["logs", "full-logs", accessToken, normalizedFilters] as const,
    queryFn: () => listFullLogs(accessToken, normalizedFilters),
    retry: false,
    refetchInterval: refreshInterval,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: refreshInterval !== false,
  };
};
export const listFullLogSessions = (accessToken: string, query: FullLogFilters) =>
  apiClient.get<components["schemas"]["FullLogSessionPage"]>("/logs/full/sessions", {
    accessToken,
    query,
    cache: "no-store",
  });

export const fullLogSessionsQueryOptions = (
  accessToken: string,
  filters: FullLogFilters,
  refreshInterval: number | false = false,
) => ({
  ...fullLogsQueryOptions(accessToken, filters, refreshInterval),
  queryKey: ["logs", "full-sessions", accessToken, normalizeFullLogFilters(filters)] as const,
  queryFn: () => listFullLogSessions(accessToken, normalizeFullLogFilters(filters)),
});
export const getFullLog = (accessToken: string, id: string) =>
  apiClient.get<FullLogRecord>(`/logs/full/${encodeURIComponent(id)}`, { accessToken, cache: "no-store" });
export const fullLogStorage = (accessToken: string) =>
  apiClient.get<components["schemas"]["FullLogStorageStats"]>("/logs/full/storage", { accessToken });
export const clearFullLogs = (accessToken: string, days: number | null) =>
  apiClient.delete<{ deleted: number }>("/logs/full", {
    accessToken,
    query: days == null ? {} : { older_than_days: days },
  });
