/** 单卡凭据刷新和访问令牌验证，不自动重试或回退到其他卡片。 */
import { apiClient } from "@/components/networking";
import type { components } from "@/lib/http/schema";
export type CredentialRefreshResult = components["schemas"]["AccountPoolCredentialRefreshResult"];
export type AccessFallbackResult = components["schemas"]["AccountPoolAccessTokenFallbackVerification"];
export const refreshCardCredentials = (accessToken: string, id: string, version: number) =>
  apiClient.post<CredentialRefreshResult>(`/account_pool/environments/${encodeURIComponent(id)}/credentials/refresh`, {
    accessToken,
    body: { version, confirmed: true },
  });
export const verifyCardAccess = (accessToken: string, id: string, model: string) =>
  apiClient.post<AccessFallbackResult>(
    `/account_pool/environments/${encodeURIComponent(id)}/access-token-fallback/verify`,
    { accessToken, body: { model, confirmed: true } },
  );
