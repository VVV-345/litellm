// 记忆管理接口；共用客户端状态，保持既有请求契约。
import { proxyBaseUrl, globalLitellmHeaderName } from "./clientState";

// ============================================================
// Memory management (/v1/memory)
// ============================================================

/**
 * Encode a memory key for use in a URL path segment.
 *
 * The backend route is declared as `/v1/memory/{key:path}`, which supports
 * slashes in the key (e.g. `user/123/notes`). Plain `encodeURIComponent`
 * encodes `/` as `%2F`, and some proxies/middlewares (nginx default,
 * CloudFlare, AWS ALB) either reject or silently re-decode `%2F`, which
 * can break the request before FastAPI ever sees it.
 *
 * We keep slashes literal as path delimiters while still encoding every
 * other potentially-unsafe character (spaces, `?`, `#`, `%`, etc.) per
 * path segment.
 */
const encodeMemoryKeyForPath = (key: string): string => key.split("/").map(encodeURIComponent).join("/");

export interface MemoryRow {
  memory_id: string;
  key: string;
  value: string;
  metadata?: unknown;
  user_id?: string | null;
  team_id?: string | null;
  created_at?: string;
  created_by?: string | null;
  updated_at?: string;
  updated_by?: string | null;
}

export interface MemoryListResponse {
  memories: MemoryRow[];
  total: number;
}

export const fetchMemoryList = async (
  accessToken: string,
  options: {
    key?: string;
    keyPrefix?: string;
    page?: number;
    pageSize?: number;
  } = {},
): Promise<MemoryListResponse> => {
  const base = proxyBaseUrl ? `${proxyBaseUrl}/v1/memory` : `/v1/memory`;
  const params = new URLSearchParams();
  // keyPrefix takes precedence — backend also does, but we omit `key`
  // to keep the URL clean and intent obvious.
  if (options.keyPrefix) {
    params.append("key_prefix", options.keyPrefix);
  } else if (options.key) {
    params.append("key", options.key);
  }
  if (options.page != null) params.append("page", String(options.page));
  if (options.pageSize != null) params.append("page_size", String(options.pageSize));
  const url = params.toString() ? `${base}?${params.toString()}` : base;
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

export const createMemory = async (
  accessToken: string,
  payload: { key: string; value: string; metadata?: unknown },
): Promise<MemoryRow> => {
  const url = proxyBaseUrl ? `${proxyBaseUrl}/v1/memory` : `/v1/memory`;
  const body: Record<string, unknown> = {
    key: payload.key,
    value: payload.value,
  };
  if (payload.metadata !== undefined) body.metadata = payload.metadata;
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

export const updateMemory = async (
  accessToken: string,
  key: string,
  payload: { value?: string; metadata?: unknown },
): Promise<MemoryRow> => {
  const encoded = encodeMemoryKeyForPath(key);
  const url = proxyBaseUrl ? `${proxyBaseUrl}/v1/memory/${encoded}` : `/v1/memory/${encoded}`;
  const response = await fetch(url, {
    method: "PUT",
    headers: {
      [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const errorData = await response.text();
    throw new Error(errorData);
  }
  return response.json();
};

export const deleteMemory = async (accessToken: string, key: string): Promise<void> => {
  const encoded = encodeMemoryKeyForPath(key);
  const url = proxyBaseUrl ? `${proxyBaseUrl}/v1/memory/${encoded}` : `/v1/memory/${encoded}`;
  const response = await fetch(url, {
    method: "DELETE",
    headers: {
      [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
  });
  if (!response.ok) {
    const errorData = await response.text();
    throw new Error(errorData);
  }
};
