/** 本文件集中定义号池表单与代理 Profile 的边界校验。 */

import type { AccountPoolEnvironment, AccountPoolProxyProfile, AccountPoolUpdateRequest } from "./AccountPoolTypes";

const SAFE_PROXY_PROTOCOLS: ReadonlySet<string> = new Set(["http:", "https:"]);

const normalizeProtocol = (value: string): string => {
  const normalized = value.trim().toLowerCase();
  return normalized.endsWith(":") ? normalized : `${normalized}:`;
};

const profileProtocol = (profile: AccountPoolProxyProfile): string | null => {
  const declaredProtocol = profile.protocol ?? profile.scheme;
  if (declaredProtocol) return normalizeProtocol(declaredProtocol);
  const rawUrl = profile.proxy_url ?? profile.url;
  if (!rawUrl) return null;
  try {
    return new URL(rawUrl).protocol.toLowerCase();
  } catch {
    return "invalid:";
  }
};

export const validateProxyProfileSelection = (
  proxyMode: AccountPoolUpdateRequest["proxy_mode"],
  proxyProfileId: string | null,
  profiles: readonly AccountPoolProxyProfile[],
): string | null => {
  if (proxyMode === "default_gateway") return null;
  if (!proxyProfileId) return "请选择代理 Profile";
  if (profiles.length === 0) return "暂无可用代理 Profile，请先配置 Profile";
  const selectedProfile = profiles.find((profile) => profile.id === proxyProfileId);
  if (!selectedProfile) return "所选代理 Profile 已删除，请重新选择";
  const protocol = profileProtocol(selectedProfile);
  if (protocol === "invalid:") return "代理 Profile URL 无效";
  if (protocol !== null && !SAFE_PROXY_PROTOCOLS.has(protocol)) {
    return "代理 Profile URL 协议不安全，仅支持 HTTP、HTTPS 或 SOCKS5";
  }
  return null;
};

export const validateAccountPoolUpdate = (
  form: AccountPoolUpdateRequest,
  environment: AccountPoolEnvironment,
  profiles: readonly AccountPoolProxyProfile[],
): string | null => {
  if (!form.name.trim()) return "请输入环境名称";
  if (!Number.isInteger(form.concurrency_limit) || form.concurrency_limit < 1 || form.concurrency_limit > 1000) {
    return "并发数必须是 1 到 1000 之间的整数";
  }
  const unsupportedModel = form.enabled_models.find((model) => !environment.available_models.includes(model));
  if (unsupportedModel) return `模型 ${unsupportedModel} 当前不可用`;
  return validateProxyProfileSelection(form.proxy_mode, form.proxy_profile_id, profiles);
};
