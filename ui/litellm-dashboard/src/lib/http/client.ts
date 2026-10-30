import { notifyDashboardDataChanged } from "../cacheEvents";
import type { SessionFailure } from "../sessionExpiry";
import { cachedDashboardRequest } from "./responseCache";
import { getSessionToken } from "./runtime";

export type HttpMethod = "GET" | "POST" | "PUT" | "DELETE" | "PATCH";

export type QueryValue = string | number | boolean | null | undefined;

export type QueryParams = Record<string, QueryValue | QueryValue[]>;

export interface RequestOptions {
  /** Bearer token. When present, the auth header is set automatically. */
  accessToken?: string | null;
  /** Serialized to JSON unless `rawBody` is provided. */
  body?: unknown;
  /** Sent verbatim (FormData, Blob, pre-stringified text); disables JSON handling. */
  rawBody?: BodyInit;
  query?: QueryParams;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  cache?: RequestCache;
  invalidateOnSuccess?: boolean;
}

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

/**
 * Best-effort extraction of a human-readable message from a proxy error body.
 * Lives here because error parsing is the client's job; networking.tsx re-exports
 * it so existing `@/components/networking` import paths keep working.
 */
const deriveDetailMessage = (detail: any): string | undefined => {
  if (Array.isArray(detail)) return detail.map((d: any) => d?.msg || JSON.stringify(d)).join("; ");
  if (typeof detail === "string") return detail;
  if (typeof detail?.error === "string") return detail.error;
  if (detail && typeof detail === "object") return detail.error?.message || detail.message;
  return undefined;
};

export const deriveErrorMessage = (errorData: any): string => {
  const detailStr = deriveDetailMessage(errorData?.detail);
  return (
    (errorData?.error &&
      (errorData.error.message || (typeof errorData.error === "string" ? errorData.error : undefined))) ||
    errorData?.message ||
    detailStr ||
    JSON.stringify(errorData)
  );
};

/**
 * The proxy serializes HTTPException details as the string form of a Python dict,
 * so a rejection reaches the UI as "{'error': 'actual message'}" (or that string
 * nested inside the JSON error envelope). Unwraps to the actual message; returns
 * the input unchanged when it does not match a known wrapper shape.
 */
export const unwrapProxyErrorMessage = (raw: string): string => {
  const trimmed = raw.trim();
  try {
    const parsed = JSON.parse(trimmed);
    if (parsed && typeof parsed === "object") {
      const derived = deriveErrorMessage(parsed);
      if (typeof derived === "string" && derived !== trimmed) {
        return unwrapProxyErrorMessage(derived);
      }
    }
  } catch {
    const pythonDictMatch = trimmed.match(/^\{'error':\s*(['"])([\s\S]*)\1\}$/);
    if (pythonDictMatch) {
      return pythonDictMatch[2];
    }
  }
  return raw;
};

export const extractProxyErrorMessage = (error: unknown): string => {
  if (error instanceof Error) {
    return unwrapProxyErrorMessage(error.message);
  }
  return unwrapProxyErrorMessage(String(error));
};

export interface ApiClientConfig {
  /** Resolves the API origin at call time (it can change at runtime). */
  getBaseUrl: () => string;
  /** Resolves the auth header name at call time. Defaults to "Authorization". */
  getAuthHeaderName?: () => string;
  /** Invoked with status and the session cookie from the start of the request. Fire-and-forget. */
  onError?: (failure: SessionFailure) => void | Promise<void>;
  /** Injectable fetch implementation; defaults to the global. */
  fetchImpl?: typeof fetch;
}

export interface ApiClient {
  request<T = any>(method: HttpMethod, path: string, options?: RequestOptions): Promise<T>;
  requestBlob(method: HttpMethod, path: string, options?: RequestOptions): Promise<Blob>;
  get<T = any>(path: string, options?: RequestOptions): Promise<T>;
  post<T = any>(path: string, options?: RequestOptions): Promise<T>;
  put<T = any>(path: string, options?: RequestOptions): Promise<T>;
  delete<T = any>(path: string, options?: RequestOptions): Promise<T>;
  patch<T = any>(path: string, options?: RequestOptions): Promise<T>;
}

const appendQuery = (url: string, query: QueryParams | undefined): string => {
  if (!query) return url;
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      value.forEach((v) => v !== undefined && v !== null && search.append(key, String(v)));
    } else {
      search.append(key, String(value));
    }
  }
  const qs = search.toString();
  if (!qs) return url;
  return url.includes("?") ? `${url}&${qs}` : `${url}?${qs}`;
};

export const fetchDashboardBuildId = async (url: string, signal?: AbortSignal): Promise<string | null> => {
  const response = await fetch(url, {
    cache: "no-store",
    credentials: "same-origin",
    signal,
    headers: { Accept: "text/html" },
  });
  if (!response.ok || response.redirected) return null;
  const html = await response.text();
  return new DOMParser().parseFromString(html, "text/html")
    .querySelector<HTMLMetaElement>('meta[name="litellm-ui-build"]')?.content ?? null;
};

export function createApiClient(config: ApiClientConfig): ApiClient {
  const { getBaseUrl, getAuthHeaderName, onError, fetchImpl } = config;
  const doFetch: typeof fetch = async (input, init) => {
    const response = await cachedDashboardRequest(input, init, (target, options) =>
      (fetchImpl ?? globalThis.fetch)(target, { cache: "no-store", ...options }),
    );
    return response;
  };

  const reportFailure = (message: string, status: number, sessionToken: string | null) => {
    onError?.({ message, status, sessionToken });
  };

  async function request<T = any>(method: HttpMethod, path: string, options: RequestOptions = {}): Promise<T> {
    const { accessToken, body, rawBody, query, headers: extraHeaders, signal } = options;
    const sessionToken = getSessionToken();

    const url = appendQuery(`${getBaseUrl()}${path}`, query);

    const headers: Record<string, string> = {};
    if (rawBody === undefined) {
      headers["Content-Type"] = "application/json";
    }
    if (accessToken) {
      const headerName = getAuthHeaderName ? getAuthHeaderName() : "Authorization";
      headers[headerName] = `Bearer ${accessToken}`;
    }
    if (extraHeaders) {
      Object.assign(headers, extraHeaders);
    }

    const init: RequestInit = { method, headers, signal, ...(options.cache ? { cache: options.cache } : {}) };
    if (rawBody !== undefined) {
      init.body = rawBody;
    } else if (body !== undefined) {
      init.body = JSON.stringify(body);
    }

    const response = await doFetch(url, init);
    if (response.ok && method !== "GET" && options.invalidateOnSuccess !== false) notifyDashboardDataChanged();

    if (!response.ok) {
      const raw = await response.text();
      let errorBody: unknown = raw;
      let message: string;
      try {
        errorBody = JSON.parse(raw);
        message = deriveErrorMessage(errorBody);
      } catch {
        message = raw || `HTTP ${response.status}`;
      }
      reportFailure(message, response.status, sessionToken);
      throw new ApiError(message, response.status, errorBody);
    }

    const text = await response.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }

  async function requestBlob(method: HttpMethod, path: string, options: RequestOptions = {}): Promise<Blob> {
    const { accessToken, body, rawBody, query, headers: extraHeaders, signal } = options;
    const sessionToken = getSessionToken();
    const url = appendQuery(`${getBaseUrl()}${path}`, query);
    const headers: Record<string, string> = {};
    if (rawBody === undefined) headers["Content-Type"] = "application/json";
    if (accessToken) headers[getAuthHeaderName ? getAuthHeaderName() : "Authorization"] = `Bearer ${accessToken}`;
    if (extraHeaders) Object.assign(headers, extraHeaders);
    const init: RequestInit = { method, headers, signal, ...(options.cache ? { cache: options.cache } : {}) };
    if (rawBody !== undefined) init.body = rawBody;
    else if (body !== undefined) init.body = JSON.stringify(body);
    const response = await doFetch(url, init);
    if (response.ok && method !== "GET" && options.invalidateOnSuccess !== false) notifyDashboardDataChanged();
    if (!response.ok) {
      const raw = await response.text();
      let message = raw || `HTTP ${response.status}`;
      try {
        message = deriveErrorMessage(JSON.parse(raw));
      } catch {
        // Keep the plain response body when it is not JSON
      }
      reportFailure(message, response.status, sessionToken);
      throw new ApiError(message, response.status, raw);
    }
    return response.blob();
  }

  return {
    request,
    requestBlob,
    get: (path, options) => request("GET", path, options),
    post: (path, options) => request("POST", path, options),
    put: (path, options) => request("PUT", path, options),
    delete: (path, options) => request("DELETE", path, options),
    patch: (path, options) => request("PATCH", path, options),
  };
}
