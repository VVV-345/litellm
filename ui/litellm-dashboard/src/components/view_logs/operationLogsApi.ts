import { apiClient } from "@/components/networking";
import type { components } from "@/lib/http/schema";
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
export type LogDetail = components["schemas"]["ErrorLogDetail"];
type ErrorStats = components["schemas"]["ErrorStats"];
type AccountPoolLogStorageStats = components["schemas"]["AccountPoolLogStorageStats"];
type LogPage = components["schemas"]["ErrorLogPage"];
type AccountPoolLogClearResult = components["schemas"]["AccountPoolLogClearResult"];

export const listOperationLogs = (accessToken: string, query: LogFilters) =>
  apiClient.get<LogPage>("/logs/operations", { accessToken, query });

export interface OperationLogsQueryParams {
  accessToken: string;
  filters: LogFilters;
  offset: number;
  pageSize: number;
  refreshInterval?: number | false;
}

export const operationLogsQueryOptions = ({
  accessToken,
  filters,
  offset,
  pageSize,
  refreshInterval = false,
}: OperationLogsQueryParams) => {
  const pageQuery = { ...filters, offset, limit: pageSize };
  return {
    queryKey: ["logs", "logs", accessToken, filters, offset, pageSize] as const,
    queryFn: () => listOperationLogs(accessToken, pageQuery),
    retry: false,
    refetchInterval: refreshInterval,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: refreshInterval !== false,
  };
};

export const getOperationLog = (accessToken: string, eventId: string) =>
  apiClient.get<LogDetail>(`/logs/operations/${encodeURIComponent(eventId)}`, { accessToken });

export const getOperationStats = (accessToken: string, query: LogFilters = {}) =>
  apiClient.get<ErrorStats>("/logs/operations/stats", { accessToken, query });

export const clearOperationLogs = (accessToken: string, olderThanDays?: 7 | 14 | 30 | 45) =>
  apiClient.delete<AccountPoolLogClearResult>("/logs/operations", {
    accessToken,
    query: olderThanDays ? { older_than_days: olderThanDays } : undefined,
  });

export const getOperationLogStorage = (accessToken: string) =>
  apiClient.get<AccountPoolLogStorageStats>("/logs/operations/storage", { accessToken });

export const exportOperationLogs = async (accessToken: string, query: LogFilters): Promise<Blob> => {
  return apiClient.requestBlob("GET", "/logs/operations/export", { accessToken, query });
};
