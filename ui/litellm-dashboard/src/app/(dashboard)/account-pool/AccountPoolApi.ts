/** 本文件封装号池管理请求，页面组件不直接处理 URL、鉴权头或传输细节。 */

import { apiClient } from "@/components/networking";

import type {
  AccountPoolAuthorization,
  AccountPoolEnvironment,
  AccountPoolProxyProfile,
  AccountPoolUpdateRequest,
} from "./AccountPoolTypes";

export const listAccountPoolEnvironments = (accessToken: string): Promise<AccountPoolEnvironment[]> =>
  apiClient.get<AccountPoolEnvironment[]>("/account_pool/environments", { accessToken });

export const getAccountPoolEnvironment = (
  accessToken: string,
  environmentId: string,
): Promise<AccountPoolEnvironment> =>
  apiClient.get<AccountPoolEnvironment>(`/account_pool/environments/${encodeURIComponent(environmentId)}`, {
    accessToken,
  });

export const createAccountPoolEnvironment = (accessToken: string, name: string): Promise<AccountPoolAuthorization> =>
  apiClient.post<AccountPoolAuthorization>("/account_pool/environments", {
    accessToken,
    body: { name, provider: "openai" },
  });

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
