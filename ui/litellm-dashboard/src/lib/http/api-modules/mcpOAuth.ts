// MCP 授权与用户凭据接口；共用客户端状态，保持既有请求契约。
import { proxyBaseUrl, globalLitellmHeaderName, getProxyBaseUrl, apiClient } from "./clientState";
import { deriveErrorMessage } from "@/lib/http/client";
import type { MCPUserEnvVarsStatus } from "@/components/mcp_tools/types";

export const cacheTemporaryMcpServer = async (accessToken: string, payload: Record<string, any>) => {
  const url = proxyBaseUrl ? `${proxyBaseUrl}/v1/mcp/server/oauth/session` : `/v1/mcp/server/oauth/session`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  const data = await response.json();
  if (!response.ok) {
    const errorMessage = deriveErrorMessage(data) || data?.error || "Failed to cache MCP server";
    throw new Error(errorMessage);
  }
  return data;
};

interface RegisterMcpOAuthClientPayload {
  client_name?: string;
  grant_types?: string[];
  response_types?: string[];
  token_endpoint_auth_method?: string;
  redirect_uris?: string[];
}

export const registerMcpOAuthClient = async (
  accessToken: string,
  serverId: string,
  payload: RegisterMcpOAuthClientPayload,
) => {
  const base = getProxyBaseUrl();
  const normalizedServerId = encodeURIComponent(serverId.trim());
  const url = `${base}/v1/mcp/server/oauth/${normalizedServerId}/register`;

  const response = await fetch(url, {
    method: "POST",
    headers: {
      [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
    },
    body: JSON.stringify(payload),
  });

  const data = await response.json();
  if (!response.ok) {
    const errorMessage = deriveErrorMessage(data) || data?.detail || "Failed to register OAuth client";
    throw new Error(errorMessage);
  }
  return data;
};

interface BuildOAuthAuthorizeURLParams {
  serverId: string;
  clientId?: string;
  redirectUri: string;
  state: string;
  codeChallenge: string;
  scope?: string;
}

export const buildMcpOAuthAuthorizeUrl = ({
  serverId,
  clientId,
  redirectUri,
  state,
  codeChallenge,
  scope,
}: BuildOAuthAuthorizeURLParams): string => {
  const base = getProxyBaseUrl();
  const normalizedServerId = encodeURIComponent(serverId.trim());
  const url = `${base}/v1/mcp/server/oauth/${normalizedServerId}/authorize`;
  const params = new URLSearchParams({
    redirect_uri: redirectUri,
    state,
    response_type: "code",
    code_challenge: codeChallenge,
    code_challenge_method: "S256",
  });
  if (clientId && clientId.trim().length > 0) {
    params.set("client_id", clientId);
  }
  if (scope && scope.trim().length > 0) {
    params.set("scope", scope);
  }
  return `${url}?${params.toString()}`;
};

interface ExchangeMcpOAuthTokenParams {
  serverId: string;
  code: string;
  clientId?: string;
  clientSecret?: string;
  codeVerifier: string;
  redirectUri: string;
  accessToken?: string | null;
}

export const exchangeMcpOAuthToken = async ({
  serverId,
  code,
  clientId,
  clientSecret,
  codeVerifier,
  redirectUri,
  accessToken,
}: ExchangeMcpOAuthTokenParams) => {
  const base = getProxyBaseUrl();
  const normalizedServerId = encodeURIComponent(serverId.trim());
  const url = `${base}/v1/mcp/server/oauth/${normalizedServerId}/token`;

  const body = new URLSearchParams();
  body.set("grant_type", "authorization_code");
  body.set("code", code);
  if (clientId && clientId.trim().length > 0) {
    body.set("client_id", clientId);
  }
  if (clientSecret && clientSecret.trim().length > 0) {
    body.set("client_secret", clientSecret);
  }
  body.set("code_verifier", codeVerifier);
  body.set("redirect_uri", redirectUri);

  const headers: Record<string, string> = {
    "Content-Type": "application/x-www-form-urlencoded",
  };
  if (accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }

  const response = await fetch(url, {
    method: "POST",
    headers,
    body: body.toString(),
  });

  const data = await response.json();
  if (!response.ok) {
    const oauthErrorMessage =
      typeof data?.error === "string" && typeof data?.error_description === "string"
        ? `${data.error}: ${data.error_description}`
        : undefined;
    const errorMessage = oauthErrorMessage || deriveErrorMessage(data) || data?.detail || "OAuth token exchange failed";
    throw new Error(errorMessage);
  }
  return data;
};

// ── MCP OAuth user-credential helpers ────────────────────────────────────────

export interface MCPOAuthUserCredentialStatus {
  server_id: string;
  has_credential: boolean;
  expires_at?: string | null;
  is_expired: boolean;
  connected_at?: string | null;
}

export interface MCPUserCredentialListItem {
  server_id: string;
  server_name?: string | null;
  alias?: string | null;
  credential_type: string;
  has_credential: boolean;
  expires_at?: string | null;
  connected_at?: string | null;
}

export const storeMCPOAuthUserCredential = async (
  accessToken: string,
  serverId: string,
  tokenResponse: { access_token: string; refresh_token?: string; expires_in?: number; scopes?: string[] },
): Promise<MCPOAuthUserCredentialStatus> => {
  const url = proxyBaseUrl
    ? `${proxyBaseUrl}/v1/mcp/server/${serverId}/oauth-user-credential`
    : `/v1/mcp/server/${serverId}/oauth-user-credential`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(tokenResponse),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    const errObj = err as { detail?: unknown };
    const detail = errObj?.detail;
    const detailMsg = Array.isArray(detail)
      ? detail
          .map((d: unknown) =>
            d && typeof d === "object" ? (d as Record<string, unknown>).msg ?? JSON.stringify(d) : String(d),
          )
          .join("; ")
      : typeof detail === "string"
        ? detail
        : detail && typeof (detail as Record<string, unknown>).error === "string"
          ? ((detail as Record<string, unknown>).error as string)
          : undefined;
    throw new Error(detailMsg || "Failed to store OAuth credential");
  }
  return response.json();
};

export const deleteMCPOAuthUserCredential = async (
  accessToken: string,
  serverId: string,
): Promise<MCPOAuthUserCredentialStatus> => {
  const url = proxyBaseUrl
    ? `${proxyBaseUrl}/v1/mcp/server/${serverId}/oauth-user-credential`
    : `/v1/mcp/server/${serverId}/oauth-user-credential`;
  const response = await fetch(url, {
    method: "DELETE",
    headers: { [globalLitellmHeaderName]: `Bearer ${accessToken}` },
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    const errObj = err as { detail?: unknown };
    const detail = errObj?.detail;
    const detailMsg = Array.isArray(detail)
      ? detail
          .map((d: unknown) =>
            d && typeof d === "object" ? (d as Record<string, unknown>).msg ?? JSON.stringify(d) : String(d),
          )
          .join("; ")
      : typeof detail === "string"
        ? detail
        : detail && typeof (detail as Record<string, unknown>).error === "string"
          ? ((detail as Record<string, unknown>).error as string)
          : undefined;
    throw new Error(detailMsg || "Failed to revoke OAuth credential");
  }
  return response.json();
};

export const getMCPOAuthUserCredentialStatus = async (
  accessToken: string,
  serverId: string,
): Promise<MCPOAuthUserCredentialStatus> => {
  const url = proxyBaseUrl
    ? `${proxyBaseUrl}/v1/mcp/server/${serverId}/oauth-user-credential/status`
    : `/v1/mcp/server/${serverId}/oauth-user-credential/status`;
  const response = await fetch(url, {
    method: "GET",
    headers: { [globalLitellmHeaderName]: `Bearer ${accessToken}` },
  });
  if (!response.ok) {
    return { server_id: serverId, has_credential: false, is_expired: false };
  }
  return response.json();
};

export const listMCPUserCredentials = async (accessToken: string): Promise<MCPUserCredentialListItem[]> => {
  const url = proxyBaseUrl ? `${proxyBaseUrl}/v1/mcp/user-credentials` : `/v1/mcp/user-credentials`;
  const response = await fetch(url, {
    method: "GET",
    headers: { [globalLitellmHeaderName]: `Bearer ${accessToken}` },
  });
  if (!response.ok) return [];
  return response.json();
};

// ============================================================
// MCP per-user env vars (/v1/mcp/server/{id}/user-env-vars)
// ============================================================

export const getMCPUserEnvVars = async (accessToken: string, serverId: string): Promise<MCPUserEnvVarsStatus> => {
  return apiClient.get<MCPUserEnvVarsStatus>(`/v1/mcp/server/${serverId}/user-env-vars`, { accessToken });
};

export const storeMCPUserEnvVars = async (
  accessToken: string,
  serverId: string,
  values: Record<string, string>,
): Promise<MCPUserEnvVarsStatus> => {
  return apiClient.post<MCPUserEnvVarsStatus>(`/v1/mcp/server/${serverId}/user-env-vars`, {
    accessToken,
    body: { values },
  });
};

export const listMCPUserEnvVarStatus = async (accessToken: string): Promise<MCPUserEnvVarsStatus[]> => {
  // Best-effort status badges: a failure here must not break the page, so fall
  // back to an empty list rather than surfacing the error to the caller.
  try {
    return await apiClient.get<MCPUserEnvVarsStatus[]>("/v1/mcp/user-env-vars/status", { accessToken });
  } catch {
    return [];
  }
};
