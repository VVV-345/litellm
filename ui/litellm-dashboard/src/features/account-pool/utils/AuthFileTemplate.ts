/** 把手动表单转换为非敏感模板，只合并用户明确选择的认证元数据。 */
import i18n from "@/i18n";
import type { AccountPoolSettings } from "../api/AccountPoolManagementApi";

export type AuthFileTemplate = NonNullable<AccountPoolSettings["auth_file_template"]>;
export interface AuthFileTemplateForm {
  applyPriority: boolean;
  priority: string;
  applyPrefix: boolean;
  prefix: string;
  applyProxy: boolean;
  proxyProfileId: string;
}

export const authFileTemplateToForm = (template?: AuthFileTemplate | null): AuthFileTemplateForm => ({
  applyPriority: template?.priority != null,
  priority: String(template?.priority ?? 0),
  applyPrefix: template?.prefix != null,
  prefix: template?.prefix ?? "",
  applyProxy: template?.proxy_profile_id != null,
  proxyProfileId: template?.proxy_profile_id ?? "",
});

export function authFileTemplateFromForm(form: AuthFileTemplateForm): AuthFileTemplate {
  const priority = Number(form.priority);
  if (
    form.applyPriority &&
    (!/^-?\d+$/.test(form.priority) || !Number.isInteger(priority) || priority < -2147483648 || priority > 2147483647)
  ) {
    throw new Error(i18n.t("accountPool.inputValidation.priorityInt32"));
  }
  if (form.applyPrefix && form.prefix.length > 256) throw new Error(i18n.t("accountPool.inputValidation.prefixLength"));
  return {
    priority: form.applyPriority ? priority : null,
    prefix: form.applyPrefix ? form.prefix : null,
    proxy_profile_id: form.applyProxy ? form.proxyProfileId : null,
  };
}

export const authFileTemplateFields = (template: AuthFileTemplate): Record<string, string | number> => ({
  ...(template.priority != null ? { priority: template.priority } : {}),
  ...(template.prefix != null ? { prefix: template.prefix } : {}),
  ...(template.proxy_profile_id != null ? { proxy_profile_id: template.proxy_profile_id } : {}),
});

export function mergeAuthFileTemplate(content: string, template: AuthFileTemplate): string {
  const input = content.trimStart();
  if (!input.startsWith("{") && !input.startsWith("[") && input.includes("----")) {
    throw new Error(i18n.t("accountPool.inputValidation.templateNotForText"));
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(content);
  } catch {
    throw new Error(i18n.t("accountPool.inputValidation.credentialJson"));
  }
  if (Array.isArray(parsed) && parsed.length > 0) {
    throw new Error(i18n.t("accountPool.inputValidation.singleAccountTemplate"));
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(i18n.t("accountPool.inputValidation.credentialJson"));
  }
  if ("accounts" in parsed) throw new Error(i18n.t("accountPool.inputValidation.singleAccountTemplate"));
  return JSON.stringify({
    ...parsed,
    ...(template.priority != null ? { priority: template.priority } : {}),
    ...(template.prefix != null ? { prefix: template.prefix } : {}),
    ...(template.proxy_profile_id === "" ? { proxy_url: "" } : {}),
  });
}
