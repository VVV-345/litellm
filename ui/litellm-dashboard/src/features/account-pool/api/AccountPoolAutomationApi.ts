/** 授权自动化请求仅走管理员接口，任务查询不包含密码、TOTP 或浏览器票据。 */
import { ApiError } from "@/lib/http/client";
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
export type LoginScriptStatus = components["schemas"]["LoginScriptStatus"];
export type LoginScriptPackage = components["schemas"]["LoginScriptPackage"];
const root = "/account_pool/oauth-automation";

export const getAutomation = (accessToken: string) =>
  apiClient.get<AutomationView>(root, { accessToken, cache: "no-store" });
export const checkAutomationImage = (accessToken: string) =>
  apiClient.post<AutomationView>(`${root}/self-check`, { accessToken });
export const saveAutomationSettings = (
  accessToken: string, version: number, settings: AutomationSettings, expectedSettings?: AutomationSettings,
) => apiClient.put<AutomationView>(`${root}/settings`, {
  accessToken, body: { version, settings, ...(expectedSettings ? { expected_settings: expectedSettings } : {}) },
});
export const getAutomationLoginScript = (accessToken: string) =>
  apiClient.get<LoginScriptStatus>(`${root}/login-script`, { accessToken, cache: "no-store" });
export const updateAutomationLoginScript = (accessToken: string, publisherKey: string, bundle: LoginScriptPackage | null) =>
  apiClient.put<LoginScriptStatus>(`${root}/login-script`, { accessToken, body: { publisher_key: publisherKey, package: bundle } });
export const createAutomationProfile = (accessToken: string, body: ProfileInput) =>
  apiClient.post<AutomationProfile>(`${root}/profiles`, { accessToken, body });
export const updateAutomationProfile = (accessToken: string, id: string, body: ProfileUpdate) =>
  apiClient.put<AutomationProfile>(`${root}/profiles/${encodeURIComponent(id)}`, { accessToken, body });
export const deleteAutomationProfile = (accessToken: string, id: string) =>
  apiClient.delete<AutomationView>(`${root}/profiles/${encodeURIComponent(id)}`, { accessToken });
export const forgetAutomationLogin = (accessToken: string, id: string) =>
  apiClient.delete<AutomationView>(`${root}/profiles/${encodeURIComponent(id)}/login`, { accessToken });
export const releaseAutomationReservation = (accessToken: string, id: string) =>
  apiClient.delete<AutomationView>(`${root}/profiles/${encodeURIComponent(id)}/reservation`, { accessToken });
export const submitAutomation = (accessToken: string, body: JobInput) =>
  apiClient.post<AutomationJob>(`${root}/jobs`, { accessToken, body });
export const reauthorizeAutomation = (accessToken: string, environmentId: string) =>
  apiClient.post<AutomationJob | null>(`${root}/environments/${encodeURIComponent(environmentId)}/reauthorize`, { accessToken });
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

const operatorErrorCodes = new Set([
  "account_already_bound",
  "automation_not_ready",
  "card_not_empty",
  "card_not_ready",
  "configuration_pending",
  "identity_conflict",
  "identity_mismatch",
  "image_check_failed",
  "image_not_checked",
  "image_not_configured",
  "invalid_credentials",
  "invalid_request",
  "job_busy",
  "job_exists",
  "profile_transfer_required",
  "profile_unavailable",
  "proxy_unavailable",
  "reauthorization_not_ready",
  "screen_not_available",
  "session_expired",
  "settings_conflict",
  "takeover_not_available",
  "verification_in_progress",
]);

export function safeAutomationOperationMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "号池登录已过期，请重新登录后台；不会重新提交 OAuth 登录";
    if (error.status === 403) return "没有执行授权自动化操作的权限";
    const body = error.body;
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : null;
    // Gateway 只会为审核过的固定提示返回此结构；任意上游字符串仍不公开。
    if (detail && typeof detail === "object") {
      const code = "code" in detail ? detail.code : null;
      const message = "message" in detail ? detail.message : null;
      if (typeof code === "string" && operatorErrorCodes.has(code)) {
        if (typeof message === "string" && message.length <= 300) return message;
      }
    }
    if ([404, 409].includes(error.status)) return error.message;
  }
  return "操作未完成，请刷新任务或检查镜像自检、卡片与代理状态";
}
