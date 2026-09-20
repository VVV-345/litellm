/** 自动化上号的类型和传输，凭据只保留在当前操作内存中。 */
import { apiClient } from "@/components/networking";
import type { components } from "@/lib/http/schema";

export type OnboardingItem = components["schemas"]["OnboardingItem"];
export type OnboardingImport = components["schemas"]["OnboardingImport"];
export type OnboardingPreview = components["schemas"]["OnboardingPreview"];
export type OnboardingSecrets = components["schemas"]["OnboardingSecrets"];
export type OnboardingTarget = components["schemas"]["OnboardingTarget"];
type OnboardingTargetView = components["schemas"]["OnboardingTargetView"];
export type OnboardingAction = components["schemas"]["OnboardingAction"];
export type OnboardingAuthorization = components["schemas"]["OnboardingAuthorization"];
const base = "/account_pool/onboarding";

export const listOnboarding = (accessToken: string) =>
  apiClient.get<OnboardingItem[]>(`${base}/items`, { accessToken });
export const previewOnboarding = (accessToken: string, body: OnboardingImport) =>
  apiClient.post<OnboardingPreview[]>(`${base}/preview`, { accessToken, body });
export const submitOnboarding = (accessToken: string, body: OnboardingImport) =>
  apiClient.post<components["schemas"]["OnboardingImportResult"]>(`${base}/imports`, { accessToken, body });
export const actOnboarding = (accessToken: string, id: string, body: OnboardingAction) =>
  apiClient.post<OnboardingItem>(`${base}/items/${encodeURIComponent(id)}/action`, { accessToken, body });
export const revealOnboarding = (accessToken: string, id: string) =>
  apiClient.post<OnboardingSecrets>(`${base}/items/${encodeURIComponent(id)}/secrets`, { accessToken });
export const authorizeOnboarding = (accessToken: string, id: string) =>
  apiClient.get<OnboardingAuthorization>(`${base}/items/${encodeURIComponent(id)}/authorization`, { accessToken });
export const listOnboardingTargets = (accessToken: string) =>
  apiClient.get<OnboardingTargetView[]>(`${base}/targets`, { accessToken });
export const saveOnboardingTarget = (accessToken: string, body: OnboardingTarget) =>
  apiClient.put<OnboardingTarget>(`${base}/targets`, { accessToken, body });

type OnboardingSupplierOption = components["schemas"]["OnboardingSupplierOption"];
export const listOnboardingSuppliers = (accessToken: string) =>
  apiClient.get<OnboardingSupplierOption[]>(`${base}/suppliers`, { accessToken });

export const onboardingStates: Record<OnboardingItem["state"], string> = {
  awaiting_mailbox: "待准备邮箱",
  standby: "待授权库存",
  queued: "排队中",
  running: "验证中",
  awaiting_authorization: "待完成授权",
  ready: "已就绪",
  cooling_down: "冷却中",
  disabled: "已暂停",
  failed: "失败",
};

export function parseMailboxAccounts(text: string): OnboardingImport["entries"] {
  const values: unknown = JSON.parse(text);
  if (!Array.isArray(values) || values.length === 0 || values.length > 100) {
    throw new Error("请提供包含 1～100 个账号的 JSON 数组");
  }
  return values.map((value: unknown) => {
    if (typeof value !== "object" || value === null) throw new Error("账号必须为 JSON 对象");
    const validEmail = "email" in value && typeof value.email === "string";
    const validPassword =
      "mailbox_password" in value && typeof value.mailbox_password === "string" && Boolean(value.mailbox_password);
    if (!validEmail || !validPassword) {
      throw new Error("每个账号必须包含 email 和 mailbox_password，供应商密码可填 supplier_password");
    }
    const supplierPassword = "supplier_password" in value ? value.supplier_password : "";
    if (typeof supplierPassword !== "string") throw new Error("supplier_password 必须是字符串");
    return {
      label: (value.email as string).trim(),
      content: "",
      mailbox_password: value.mailbox_password as string,
      supplier_password: supplierPassword,
    };
  });
}
