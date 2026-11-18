/** 人民币台账接口，复用管理员客户端与生成契约。 */
import { apiClient } from "@/components/networking";
import type { components } from "@/lib/http/schema";

export type CostCreate = components["schemas"]["PoolCostCreate"];
export type CostRecord = components["schemas"]["PoolCostRecord"];
export type CostReport = components["schemas"]["PoolCostReport"];
export type CostCategory = CostCreate["category"];
export const getCosts = (accessToken: string, startsOn: string, endsOn: string) =>
  apiClient.get<CostReport>(`/account_pool/costs?${new URLSearchParams({ starts_on: startsOn, ends_on: endsOn })}`, {
    accessToken,
  });
export const createCost = (accessToken: string, body: CostCreate) =>
  apiClient.post<CostRecord>("/account_pool/costs", { accessToken, body });
export const voidCost = (accessToken: string, id: string, reason: string) =>
  apiClient.post<CostRecord>(`/account_pool/costs/${encodeURIComponent(id)}/void`, { accessToken, body: { reason } });

export const stopCostRenewal = (accessToken: string, id: string) =>
  apiClient.post<CostRecord>(`/account_pool/costs/${encodeURIComponent(id)}/stop-renewal`, { accessToken });
