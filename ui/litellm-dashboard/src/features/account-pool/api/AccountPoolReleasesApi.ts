/** 本文件封装项目备份版本接口，浏览器只提交确认票据，不持有部署令牌。 */
import { apiClient } from "@/components/networking";
import type { components } from "@/lib/http/schema";

export type ReleaseView = components["schemas"]["ReleaseView"];
export type ReleaseAction = Omit<components["schemas"]["ReleaseAction-Input"], "text" | "force" | "force_acknowledgement"> & {
  text?: string;
  force?: boolean;
  force_acknowledgement?: string;
};
export type ReleaseConfirmation = components["schemas"]["ReleaseConfirmation"];
export type ReleaseJob = components["schemas"]["ReleaseJob"];
export type ReleaseVersion = components["schemas"]["ReleaseVersion"];
export type ReleaseCommands = components["schemas"]["ReleaseCommands"];
export type AutoUpdateView = components["schemas"]["AutoUpdateView"];
export type AutoUpdateSettings = components["schemas"]["AutoUpdateSettings"];

export const listReleases = (accessToken: string) =>
  apiClient.get<ReleaseView>("/account_pool/releases", { accessToken });
export const prepareRelease = (accessToken: string, body: ReleaseAction) =>
  apiClient.post<ReleaseConfirmation>("/account_pool/releases/prepare", { accessToken, body });
export const executeRelease = (accessToken: string, token: string, acknowledgement = "") =>
  apiClient.post<ReleaseJob>("/account_pool/releases/execute", { accessToken, body: { token, acknowledgement } });
export const releaseCommands = (accessToken: string, id: string) =>
  apiClient.get<ReleaseCommands>(`/account_pool/releases/${encodeURIComponent(id)}/commands`, { accessToken });
export const getAutoUpdate = (accessToken: string) =>
  apiClient.get<AutoUpdateView>("/account_pool/releases/auto-update", { accessToken });
export const saveAutoUpdate = (accessToken: string, body: AutoUpdateSettings) =>
  apiClient.post<AutoUpdateView>("/account_pool/releases/auto-update", { accessToken, body });
export const checkAutoUpdate = (accessToken: string) =>
  apiClient.post<AutoUpdateView>("/account_pool/releases/auto-update/check", { accessToken });
