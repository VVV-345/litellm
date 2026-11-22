/** 邮箱换绑仅通过管理员接口执行，上传资料不写入查询缓存或浏览器持久存储。 */
import { apiClient } from "@/components/networking";
import type { components } from "@/lib/http/schema";
import { attachAccountPoolOAuthBrowserSession } from "./AccountPoolManagementApi";
import type { LoginScriptPackage, LoginScriptStatus } from "./AccountPoolAutomationApi";

export type BindingView = components["schemas"]["BindingView"];
export type BindingPoolView = components["schemas"]["BindingPoolView"];
type ImportResult = components["schemas"]["PoolImportResult"];
type Action = components["schemas"]["BindingAction"]["action"] | "abort";
const root = "/account_pool/email-rebinding";

export const getEmailBindings = (accessToken: string) =>
  apiClient.get<BindingPoolView>(root, { accessToken, cache: "no-store" });
export const uploadEmailPool = (accessToken: string, content: string, filename: string) =>
  apiClient.post<ImportResult>(`${root}/mailboxes`, { accessToken, body: { content, filename } });
export const exportEmailBindingStatus = (accessToken: string) =>
  apiClient.requestBlob("GET", `${root}/export`, { accessToken, cache: "no-store" });
export const proposeEmailBinding = (accessToken: string, id: string, environmentId: string, mailboxId?: string) =>
  apiClient.post<BindingView>(`${root}/jobs`, {
    accessToken,
    body: { id, environment_id: environmentId, ...(mailboxId ? { mailbox_id: mailboxId } : {}) },
  });
export const confirmEmailBinding = (accessToken: string, job: BindingView) =>
  apiClient.post<BindingView>(`${root}/jobs/${encodeURIComponent(job.id)}/confirm`, {
    accessToken,
    body: { version: job.version, confirmed: true },
  });
export const actOnEmailBinding = (accessToken: string, id: string, action: Action) =>
  apiClient.post<BindingView>(`${root}/jobs/${encodeURIComponent(id)}/action`, { accessToken, body: { action } });
export const provideEmailBindingCode = (accessToken: string, id: string, code: string) =>
  apiClient.post<BindingView>(`${root}/jobs/${encodeURIComponent(id)}/code`, { accessToken, body: { code } });
export const openEmailBindingScreen = async (accessToken: string, id: string) => {
  const grant = await apiClient.post<components["schemas"]["AutomationScreen"]>(
    `${root}/jobs/${encodeURIComponent(id)}/screen`,
    { accessToken },
  );
  return attachAccountPoolOAuthBrowserSession(grant);
};
export const getEmailBindingScript = (accessToken: string) =>
  apiClient.get<LoginScriptStatus>(`${root}/script`, { accessToken, cache: "no-store" });
export const updateEmailBindingScript = (
  accessToken: string,
  publisherKey: string,
  bundle: LoginScriptPackage | null,
) =>
  apiClient.put<LoginScriptStatus>(`${root}/script`, {
    accessToken,
    body: { publisher_key: publisherKey, package: bundle },
  });
