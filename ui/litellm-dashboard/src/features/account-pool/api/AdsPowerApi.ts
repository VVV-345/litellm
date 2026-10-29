/** 封装 AdsPower 脱敏环境读取和手动同步，不在浏览器持有 Local API 凭据。 */
import { apiClient } from "@/components/networking";
import type { components } from "@/lib/http/schema";

export type AdsPowerProfiles = components["schemas"]["AccountPoolAdsPowerProfiles"];
export type AdsPowerSyncResult = components["schemas"]["AccountPoolAdsPowerSyncResult"];
export const listAdsPowerProfiles = (accessToken: string, page: number) =>
  apiClient.get<AdsPowerProfiles>(`/account_pool/adspower/profiles?page=${page}`, { accessToken });
export const syncAdsPowerProxies = (accessToken: string, profileIds: string[]) =>
  apiClient.post<AdsPowerSyncResult>("/account_pool/adspower/sync", { accessToken, body: { profile_ids: profileIds } });
