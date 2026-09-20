// 工具策略与用量接口；共用客户端状态，保持既有请求契约。
import { proxyBaseUrl, globalLitellmHeaderName, apiClient } from "./clientState";
import { deriveErrorMessage } from "@/lib/http/client";

export interface ToolRow {
  tool_id: string;
  tool_name: string;
  origin?: string;
  input_policy: string;
  output_policy: string;
  call_count?: number;
  assignments?: Record<string, any>;
  key_hash?: string;
  team_id?: string;
  key_alias?: string;
  created_at?: string;
  updated_at?: string;
  created_by?: string;
  updated_by?: string;
  user_agent?: string;
  last_used_at?: string;
}

export interface ToolPolicyOption {
  value: string;
  label: string;
  description: string;
}

export interface ToolPolicyOptionsResponse {
  input_policies: ToolPolicyOption[];
  output_policies: ToolPolicyOption[];
}

export const fetchToolPolicyOptions = async (accessToken: string): Promise<ToolPolicyOptionsResponse> => {
  const url = proxyBaseUrl ? `${proxyBaseUrl}/v1/tool/policy/options` : `/v1/tool/policy/options`;
  const response = await fetch(url, {
    method: "GET",
    headers: {
      [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
  });
  if (!response.ok) {
    const errorData = await response.text();
    throw new Error(errorData);
  }
  return response.json();
};

export const fetchToolsList = async (accessToken: string): Promise<ToolRow[]> => {
  const url = proxyBaseUrl ? `${proxyBaseUrl}/v1/tool/list` : `/v1/tool/list`;
  const response = await fetch(url, {
    method: "GET",
    headers: {
      [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
  });
  if (!response.ok) {
    const errorData = await response.text();
    throw new Error(errorData);
  }
  const data = await response.json();
  return data.tools ?? [];
};

export interface ToolSpendEntry {
  tool_name: string;
  spend: number;
  call_count: number;
  total_tokens: number;
}

export interface ToolSpendDailyEntry {
  date: string;
  tool_name: string;
  spend: number;
  call_count: number;
}

export interface ToolSpendResponse {
  by_tool: ToolSpendEntry[];
  daily: ToolSpendDailyEntry[];
  start_date: string | null;
  end_date: string | null;
}

export const getToolSpend = async (
  accessToken: string,
  startDate?: string,
  endDate?: string,
): Promise<ToolSpendResponse> =>
  apiClient.get<ToolSpendResponse>(`/v1/tool/spend`, {
    accessToken,
    query: { start_date: startDate, end_date: endDate },
  });

export interface ToolPolicyOverrideRow {
  override_id: string;
  tool_name: string;
  team_id?: string | null;
  key_hash?: string | null;
  input_policy: string;
  key_alias?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface ToolDetailResponse {
  tool: ToolRow;
  overrides: ToolPolicyOverrideRow[];
}

export interface ToolUsageLogEntry {
  id: string;
  timestamp: string;
  model?: string | null;
  spend?: number | null;
  total_tokens?: number | null;
  input_snippet?: string | null;
}

export interface ToolUsageLogsResponse {
  logs: ToolUsageLogEntry[];
  total: number;
  page: number;
  page_size: number;
}

export const getToolUsageLogs = async (
  accessToken: string,
  toolName: string,
  options: { page?: number; pageSize?: number; startDate?: string; endDate?: string },
): Promise<ToolUsageLogsResponse> => {
  const encoded = encodeURIComponent(toolName);
  const url = proxyBaseUrl ? `${proxyBaseUrl}/v1/tool/${encoded}/logs` : `/v1/tool/${encoded}/logs`;
  const params = new URLSearchParams();
  if (options.page != null) params.append("page", String(options.page));
  if (options.pageSize != null) params.append("page_size", String(options.pageSize));
  if (options.startDate) params.append("start_date", options.startDate);
  if (options.endDate) params.append("end_date", options.endDate);
  const fullUrl = params.toString() ? `${url}?${params.toString()}` : url;
  const response = await fetch(fullUrl, {
    method: "GET",
    headers: {
      [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
  });
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(deriveErrorMessage(errorData));
  }
  return response.json();
};

export const fetchToolDetail = async (accessToken: string, toolName: string): Promise<ToolDetailResponse> => {
  const encoded = encodeURIComponent(toolName);
  const url = proxyBaseUrl ? `${proxyBaseUrl}/v1/tool/${encoded}/detail` : `/v1/tool/${encoded}/detail`;
  const response = await fetch(url, {
    method: "GET",
    headers: {
      [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
  });
  if (!response.ok) {
    const errorData = await response.text();
    throw new Error(errorData);
  }
  return response.json();
};

export const updateToolPolicy = async (
  accessToken: string,
  toolName: string,
  policies: { input_policy?: string; output_policy?: string },
  options?: { team_id?: string | null; key_hash?: string | null; key_alias?: string | null },
): Promise<ToolRow> => {
  const url = proxyBaseUrl ? `${proxyBaseUrl}/v1/tool/policy` : `/v1/tool/policy`;
  const body: Record<string, string | undefined | null> = {
    tool_name: toolName,
  };
  if (policies.input_policy != null) body.input_policy = policies.input_policy;
  if (policies.output_policy != null) body.output_policy = policies.output_policy;
  if (options?.team_id != null) body.team_id = options.team_id || undefined;
  if (options?.key_hash != null) body.key_hash = options.key_hash || undefined;
  if (options?.key_alias != null) body.key_alias = options.key_alias || undefined;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const errorData = await response.text();
    throw new Error(errorData);
  }
  return response.json();
};

export const deleteToolPolicyOverride = async (
  accessToken: string,
  toolName: string,
  params: { team_id?: string | null; key_hash?: string | null },
): Promise<{ deleted: boolean; tool_name: string }> => {
  const encoded = encodeURIComponent(toolName);
  const q = new URLSearchParams();
  if (params.team_id != null && params.team_id !== "") q.set("team_id", params.team_id);
  if (params.key_hash != null && params.key_hash !== "") q.set("key_hash", params.key_hash);
  const query = q.toString();
  const url = proxyBaseUrl
    ? `${proxyBaseUrl}/v1/tool/${encoded}/overrides${query ? `?${query}` : ""}`
    : `/v1/tool/${encoded}/overrides${query ? `?${query}` : ""}`;
  const response = await fetch(url, {
    method: "DELETE",
    headers: {
      [globalLitellmHeaderName]: `Bearer ${accessToken}`,
    },
  });
  if (!response.ok) {
    const errorData = await response.text();
    throw new Error(errorData);
  }
  return response.json();
};
