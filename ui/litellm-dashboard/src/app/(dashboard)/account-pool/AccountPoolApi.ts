/** 本文件封装号池管理请求，页面组件不直接处理 URL、鉴权头或传输细节。 */

import { apiClient } from "@/components/networking";

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
  apiClient.post<AccountPoolAuthorization>("/account_pool/environments", {
    accessToken,
    body: request,
  });

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
  apiClient.post<AccountPoolEnvironment>("/account_pool/openai-compatible", { accessToken, body: request });

export const updateAccountPoolEnvironment = (
  accessToken: string,
  environmentId: string,
  request: AccountPoolUpdateRequest,
): Promise<AccountPoolEnvironment> =>
  apiClient.put<AccountPoolEnvironment>(`/account_pool/environments/${encodeURIComponent(environmentId)}`, {
    accessToken,
    body: request,
  });

export const authorizeAccountPoolEnvironment = (
  accessToken: string,
  environmentId: string,
): Promise<AccountPoolAuthorization> =>
  apiClient.post<AccountPoolAuthorization>(
    `/account_pool/environments/${encodeURIComponent(environmentId)}/authorize`,
    { accessToken },
  );

export const deleteAccountPoolEnvironment = (accessToken: string, environmentId: string): Promise<void> =>
  apiClient.delete<void>(`/account_pool/environments/${encodeURIComponent(environmentId)}`, { accessToken });

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
