/** 单卡额度操作客户端，不自动重试兑换、刷新 Token 或改选其他卡片。 */
import { apiClient } from "@/components/networking";
import type { components } from "@/lib/http/schema";

export type QuotaResetStatus = components["schemas"]["AccountPoolResetStatus"];
export type QuotaResetOperation = components["schemas"]["AccountPoolResetOperation"];
export type QuotaResetVerification = components["schemas"]["AccountPoolResetVerification"];
const root = (id: string) => `/account_pool/environments/${encodeURIComponent(id)}/quota-reset`;

export const getQuotaResetStatus = (accessToken: string, id: string) =>
  apiClient.get<QuotaResetStatus>(root(id), { accessToken });
export const prepareQuotaReset = (accessToken: string, id: string, previousId?: string) =>
  apiClient.post<QuotaResetOperation>(`${root(id)}/prepare`, {
    accessToken,
    body: { previous_request_id: previousId ?? null },
  });
export const consumeQuotaReset = (accessToken: string, id: string, requestId: string) =>
  apiClient.post<QuotaResetOperation>(`${root(id)}/consume`, {
    accessToken,
    body: { request_id: requestId, confirmed: true },
  });
export const verifyQuotaReset = (accessToken: string, id: string, model: string, recover: boolean) =>
  apiClient.post<QuotaResetVerification>(`${root(id)}/verify`, {
    accessToken,
    body: { model, recover, confirmed: true },
  });
