/** 本文件集中定义号池表单与代理 Profile 的边界校验。 */

import type { AccountPoolEnvironment, AccountPoolProxyProfile, AccountPoolUpdateRequest } from "./AccountPoolTypes";
import type { TFunction } from "i18next";

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
  t: TFunction,
  proxyMode: AccountPoolUpdateRequest["proxy_mode"],
  proxyProfileId: string | null,
  profiles: readonly AccountPoolProxyProfile[],
): string | null => {
  if (proxyMode === "default_gateway") return null;
  if (!proxyProfileId) return t("accountPool.validation.proxyProfileRequired");
  if (profiles.length === 0) return t("accountPool.validation.noProxyProfiles");
  const selectedProfile = profiles.find((profile) => profile.id === proxyProfileId);
  if (!selectedProfile) return t("accountPool.validation.proxyProfileRemoved");
  const protocol = profileProtocol(selectedProfile);
  if (protocol === "invalid:") return t("accountPool.validation.proxyProfileInvalid");
  if (protocol !== null && !SAFE_PROXY_PROTOCOLS.has(protocol)) {
    return t("accountPool.validation.proxyProfileProtocol");
  }
  return null;
};

export const validateAccountPoolUpdate = (
  t: TFunction,
  form: AccountPoolUpdateRequest,
  environment: AccountPoolEnvironment,
  profiles: readonly AccountPoolProxyProfile[],
): string | null => {
  if (!form.name.trim()) return t("accountPool.validation.nameRequired");
  if (!Number.isInteger(form.concurrency_limit) || form.concurrency_limit < 1 || form.concurrency_limit > 1000) {
    return t("accountPool.validation.concurrencyRange");
  }
  const unsupportedModel = form.enabled_models.find((model) => !environment.available_models.includes(model));
  if (unsupportedModel) return t("accountPool.validation.modelUnavailable", { model: unsupportedModel });
  return validateProxyProfileSelection(t, form.proxy_mode, form.proxy_profile_id, profiles);
};
