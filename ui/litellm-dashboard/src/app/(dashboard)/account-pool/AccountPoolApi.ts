/** 本文件封装号池管理请求，页面组件不直接处理 URL、鉴权头或传输细节。 */

import { v4 as uuidv4 } from "uuid";

import { apiClient } from "@/components/networking";
import { ApiError } from "@/lib/http/client";

import type {
  AccountPoolAuthorization,
  AccountPoolClashNode,
  AccountPoolCreateRequest,
  AccountPoolEnvironment,
  AccountPoolProxyGatewayConfiguration,
  AccountPoolProxyGateway,
  AccountPoolProxyGatewayDelay,
  AccountPoolProxyProfile,
  AccountPoolUpdateRequest,
} from "./AccountPoolTypes";

export interface AccountPoolProviderFamily {
  kind: string;
  display_name: string;
  supplier: string | null;
  authentication: string;
  available: boolean;
  description: string;
  card_count: number;
}

const isRetryableGatewayError = (error: unknown): error is ApiError =>
  error instanceof ApiError && (error.status === 502 || error.status === 504);

const runIdempotentMutation = async <T>(request: (idempotencyKey: string) => Promise<T>): Promise<T> => {
  const idempotencyKey = uuidv4();
  try {
    return await request(idempotencyKey);
  } catch (error) {
    if (!isRetryableGatewayError(error)) throw error;
    return request(idempotencyKey);
  }
};

export const listAccountPoolProviderFamilies = (accessToken: string): Promise<AccountPoolProviderFamily[]> =>
  apiClient.get<AccountPoolProviderFamily[]>("/account_pool/provider-families", { accessToken });

export const listAccountPoolEnvironments = (accessToken: string): Promise<AccountPoolEnvironment[]> =>
  apiClient.get<AccountPoolEnvironment[]>("/account_pool/environments", { accessToken });

export const getAccountPoolEnvironment = (
  accessToken: string,
  environmentId: string,
): Promise<AccountPoolEnvironment> =>
  apiClient.get<AccountPoolEnvironment>(`/account_pool/environments/${encodeURIComponent(environmentId)}`, {
    accessToken,
  });

export const createAccountPoolEnvironment = (
  accessToken: string,
  request: AccountPoolCreateRequest,
): Promise<AccountPoolAuthorization> =>
  runIdempotentMutation((idempotencyKey) =>
    apiClient.post<AccountPoolAuthorization>("/account_pool/environments", {
      accessToken,
      body: request,
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  );

export interface AccountPoolOpenAICompatibleCreateRequest {
  name: string;
  provider: "openai";
  channel: "openai_compatible";
  supplier: "openai_compatible";
  provider_family: "openai_compatible";
  openai_compatible: {
    base_url: string;
    prefix: string;
    priority: number;
    test_model: string;
    api_keys: Array<{ api_key: string; proxy_profile_id?: string; weight: number }>;
    headers: Array<[string, string]>;
    custom_models: string[];
  };
}

export const createOpenAICompatibleAccountPoolEnvironment = (
  accessToken: string,
  request: AccountPoolOpenAICompatibleCreateRequest,
): Promise<AccountPoolEnvironment> =>
  runIdempotentMutation((idempotencyKey) =>
    apiClient.post<AccountPoolEnvironment>("/account_pool/openai-compatible", {
      accessToken,
      body: request,
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  );

export interface AccountPoolDirectCredentialCreateRequest {
  name: string;
  supplier: "gemini" | "gemini_interactions";
  credential: {
    api_key: string;
    prefix: string;
    priority: number;
    weight: number;
    base_url?: string;
    headers: Array<[string, string]>;
  };
}

export const createDirectCredentialAccountPoolEnvironment = (
  accessToken: string,
  request: AccountPoolDirectCredentialCreateRequest,
): Promise<AccountPoolEnvironment> =>
  runIdempotentMutation((idempotencyKey) =>
    apiClient.post<AccountPoolEnvironment>("/account_pool/direct-credentials", {
      accessToken,
      body: request,
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  );

export const createVertexAccountPoolEnvironment = (
  accessToken: string,
  name: string,
  location: string,
  file: File,
): Promise<AccountPoolEnvironment> => {
  const form = new FormData();
  form.append("name", name);
  form.append("location", location);
  form.append("file", file, file.name);
  return runIdempotentMutation((idempotencyKey) =>
    apiClient.post<AccountPoolEnvironment>("/account_pool/vertex", {
      accessToken,
      rawBody: form,
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  );
};

export const updateAccountPoolEnvironment = (
  accessToken: string,
  environmentId: string,
  request: AccountPoolUpdateRequest,
): Promise<AccountPoolEnvironment> =>
  runIdempotentMutation((idempotencyKey) =>
    apiClient.put<AccountPoolEnvironment>(`/account_pool/environments/${encodeURIComponent(environmentId)}`, {
      accessToken,
      body: request,
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  );

export const authorizeAccountPoolEnvironment = (
  accessToken: string,
  environmentId: string,
): Promise<AccountPoolAuthorization> =>
  runIdempotentMutation((idempotencyKey) =>
    apiClient.post<AccountPoolAuthorization>(
      `/account_pool/environments/${encodeURIComponent(environmentId)}/authorize`,
      { accessToken, headers: { "Idempotency-Key": idempotencyKey } },
    ),
  );

export const deleteAccountPoolEnvironment = (accessToken: string, environmentId: string): Promise<void> =>
  runIdempotentMutation((idempotencyKey) =>
    apiClient.delete<void>(`/account_pool/environments/${encodeURIComponent(environmentId)}`, {
      accessToken,
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  );

export const listAccountPoolProxyProfiles = (accessToken: string): Promise<AccountPoolProxyProfile[]> =>
  apiClient.get<AccountPoolProxyProfile[]>("/account_pool/proxy-profiles", { accessToken });

export const listAccountPoolProxyGateways = (accessToken: string): Promise<AccountPoolProxyGateway[]> =>
  apiClient.get<AccountPoolProxyGateway[]>("/account_pool/proxy-gateways", { accessToken });

export const measureAccountPoolProxyGatewayDelays = (accessToken: string): Promise<AccountPoolProxyGatewayDelay[]> =>
  apiClient.post<AccountPoolProxyGatewayDelay[]>("/account_pool/proxy-gateways/delay", { accessToken });

export const getAccountPoolProxyGatewayConfiguration = (
  accessToken: string,
): Promise<AccountPoolProxyGatewayConfiguration> =>
  apiClient.get<AccountPoolProxyGatewayConfiguration>("/account_pool/proxy-gateways/configuration", { accessToken });

export const listAccountPoolClashNodes = (accessToken: string): Promise<AccountPoolClashNode[]> =>
  apiClient.get<AccountPoolClashNode[]>("/account_pool/proxy-gateways/nodes", { accessToken });

export const switchAccountPoolProxyGateway = (
  accessToken: string,
  port: number,
  nodeName: string,
): Promise<AccountPoolProxyGateway> =>
  apiClient.put<AccountPoolProxyGateway>(`/account_pool/proxy-gateways/${port}`, {
    accessToken,
    body: { node_name: nodeName },
  });
