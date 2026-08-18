// 本文件集中封装号池管理请求，组件不直接拼接 API 地址或认证头。
import { apiClient } from "@/components/networking";
import type {
  CreatePoolAccountRequest,
  ManagementResult,
  PoolAccount,
  ProviderServiceManifest,
  ProviderValidationResult,
} from "./types";

export const listProviderServices = (accessToken: string) =>
  apiClient.get<ProviderServiceManifest[]>("/account_pool/provider-services", { accessToken });

export const validateProviderService = (
  accessToken: string,
  body: { provider_id: string; api_base: string; api_key: string; group: string | null },
) => apiClient.post<ProviderValidationResult>("/account_pool/provider-services/validate", { accessToken, body });

export const listPoolAccounts = (accessToken: string) =>
  apiClient.get<PoolAccount[]>("/account_pool/accounts", { accessToken });

export const createPoolAccount = (accessToken: string, body: CreatePoolAccountRequest) =>
  apiClient.post<ManagementResult>("/account_pool/accounts", { accessToken, body });

export const deletePoolAccount = (accessToken: string, accountId: string) =>
  apiClient.delete<ManagementResult>(`/account_pool/accounts/${encodeURIComponent(accountId)}`, { accessToken });
