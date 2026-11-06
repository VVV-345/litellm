/** 授权自动化请求仅走管理员接口，任务查询不包含密码、TOTP 或浏览器票据。 */
import { apiClient } from "@/components/networking";
import type { components } from "@/lib/http/schema";
import { attachAccountPoolOAuthBrowserSession } from "./AccountPoolManagementApi";

export type AutomationView = components["schemas"]["AutomationView"];
export type AutomationJob = components["schemas"]["JobView"];
export type AutomationProfile = components["schemas"]["ProfileView"];
export type AutomationSettings = components["schemas"]["AutomationSettings"];
type JobInput = components["schemas"]["JobInput"];
type ProfileInput = components["schemas"]["ProfileInput"];
type ProfileUpdate = components["schemas"]["ProfileUpdate"];
type JobAction = components["schemas"]["JobAction"]["action"];
const root = "/account_pool/oauth-automation";

export const getAutomation = (accessToken: string) =>
  apiClient.get<AutomationView>(root, { accessToken, cache: "no-store" });
export const checkAutomationImage = (accessToken: string) =>
  apiClient.post<AutomationView>(`${root}/self-check`, { accessToken });
export const saveAutomationSettings = (accessToken: string, version: number, settings: AutomationSettings) =>
  apiClient.put<AutomationView>(`${root}/settings`, { accessToken, body: { version, settings } });
export const createAutomationProfile = (accessToken: string, body: ProfileInput) =>
  apiClient.post<AutomationProfile>(`${root}/profiles`, { accessToken, body });
export const updateAutomationProfile = (accessToken: string, id: string, body: ProfileUpdate) =>
  apiClient.put<AutomationProfile>(`${root}/profiles/${encodeURIComponent(id)}`, { accessToken, body });
export const deleteAutomationProfile = (accessToken: string, id: string) =>
  apiClient.delete<AutomationView>(`${root}/profiles/${encodeURIComponent(id)}`, { accessToken });
export const submitAutomation = (accessToken: string, body: JobInput) =>
  apiClient.post<AutomationJob>(`${root}/jobs`, { accessToken, body });
export const deleteAutomationJob = (accessToken: string, id: string) =>
  apiClient.delete<AutomationView>(`${root}/jobs/${encodeURIComponent(id)}`, { accessToken });
export const actOnAutomation = (accessToken: string, id: string, action: JobAction, model?: string | null) =>
  apiClient.post<AutomationJob>(`${root}/jobs/${encodeURIComponent(id)}/action`, {
    accessToken,
    body: { action, ...(model ? { model } : {}) },
  });
export const openAutomationScreen = async (accessToken: string, id: string) => {
  const grant = await apiClient.post<components["schemas"]["AutomationScreen"]>(
    `${root}/jobs/${encodeURIComponent(id)}/screen`,
    { accessToken },
  );
  return attachAccountPoolOAuthBrowserSession(grant);
};
