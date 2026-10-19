/** 把手动表单转换为非敏感模板，只合并用户明确选择的认证元数据。 */
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
    throw new Error("优先级必须是 32 位整数");
  }
  if (form.applyPrefix && form.prefix.length > 256) throw new Error("模型前缀最多 256 个字符");
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
  let parsed: unknown;
  try {
    parsed = JSON.parse(content);
  } catch {
    throw new Error("认证文件必须是有效的 JSON 对象");
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("认证文件必须是有效的 JSON 对象");
  }
  return JSON.stringify({
    ...parsed,
    ...(template.priority != null ? { priority: template.priority } : {}),
    ...(template.prefix != null ? { prefix: template.prefix } : {}),
    ...(template.proxy_profile_id === "" ? { proxy_url: "" } : {}),
  });
}
