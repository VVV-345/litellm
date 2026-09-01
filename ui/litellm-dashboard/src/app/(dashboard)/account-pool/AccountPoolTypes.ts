/** 本文件集中声明号池页面使用的 API 类型和表单载荷，避免组件重复定义协议。 */

import type { components } from "@/lib/http/schema";

type GeneratedAccountPoolEnvironment = components["schemas"]["AccountPoolEnvironment"];
type GeneratedAccountPoolAuthorization = components["schemas"]["AccountPoolAuthorization"];
type GeneratedAccountPoolUpdateRequest = components["schemas"]["AccountPoolUpdateRequest"];

export type AccountPoolEnvironment = Omit<GeneratedAccountPoolEnvironment, "version" | "configuration_pending"> & {
  version: number;
  configuration_pending: boolean;
};
export type AccountPoolAuthorization = Omit<GeneratedAccountPoolAuthorization, "environment"> & {
  environment: AccountPoolEnvironment;
};
export type AccountPoolProxyProfile = components["schemas"]["AccountPoolProxyProfile"] & {
  url?: string | null;
  proxy_url?: string | null;
  protocol?: string | null;
  scheme?: string | null;
};
export type AccountPoolUpdateRequest = Omit<GeneratedAccountPoolUpdateRequest, "proxy_profile_id"> & {
  version: number;
  proxy_profile_id: string | null;
};
export type AccountPoolQuotaSnapshot = components["schemas"]["AccountPoolQuotaSnapshot"];
export type AccountPoolQuotaWindow = components["schemas"]["AccountPoolQuotaWindow"];

export type AccountPoolStatus = AccountPoolEnvironment["status"];

export const toUpdateRequest = (
  environment: AccountPoolEnvironment,
  changes: Partial<AccountPoolUpdateRequest> = {},
): AccountPoolUpdateRequest => ({
  version: environment.version,
  name: environment.name,
  concurrency_limit: environment.concurrency_limit,
  enabled: environment.enabled,
  manual_cooldown: environment.manual_cooldown,
  proxy_mode: environment.proxy_mode,
  proxy_profile_id: environment.proxy_profile_id,
  enabled_models: environment.enabled_models,
  ...changes,
});
