/** 本文件集中定义号池权限与生命周期操作可用性。 */

import type { AccountPoolEnvironment, AccountPoolStatus } from "./AccountPoolTypes";

export const canManageAccountPool = (userRole: string | null | undefined, isViewOnly: boolean): boolean =>
  (userRole === "Admin" || userRole === "proxy_admin") && !isViewOnly;

const TRANSITIONAL_STATUSES: ReadonlySet<AccountPoolStatus> = new Set([
  "provisioning",
  "awaiting_authorization",
  "validating",
  "deleting",
]);

const AUTHORIZATION_RECOVERY_STATUSES: ReadonlySet<AccountPoolStatus> = new Set(["ready", "cooling_down", "disabled"]);

const isConfigurationPending = (environment: AccountPoolEnvironment): boolean =>
  environment.configuration_pending === true;

export const canToggleEnvironment = (environment: AccountPoolEnvironment): boolean =>
  !isConfigurationPending(environment) &&
  !TRANSITIONAL_STATUSES.has(environment.status) &&
  !(environment.status === "error" && environment.available_models.length === 0);

export const canConfigureEnvironment = (environment: AccountPoolEnvironment): boolean =>
  !isConfigurationPending(environment) &&
  !(environment.status === "provisioning" || environment.status === "validating" || environment.status === "deleting");

export const canDeleteEnvironment = (environment: AccountPoolEnvironment): boolean =>
  !isConfigurationPending(environment) && environment.status !== "deleting";

const fallbackAuthorizationAction = (
  environment: AccountPoolEnvironment,
  now: number,
): "blocked" | "recover" | "retry" | null => {
  const fallback = environment.access_token_fallback;
  if (fallback?.failure_source !== "refresh") return null;
  const expiresAt = Date.parse(fallback.expires_at ?? "");
  const verified =
    fallback.active && environment.enabled_models.some((model) => fallback.verified_models.includes(model));
  // Refresh Token 失败不代表 Access Token 失效，仍有模型可用时不发起重新授权。
  if (fallback.enabled && verified && expiresAt > now) return "blocked";
  const accessLost = expiresAt <= now || fallback.reason === "access_expired" || fallback.reason === "access_rejected";
  if (accessLost) return "recover";
  const noEnabledProof = fallback.active && fallback.verified_models.length > 0 && !verified;
  if (fallback.enabled) return noEnabledProof ? "recover" : "retry";
  return null;
};

export const canAuthorizeEnvironment = (environment: AccountPoolEnvironment): boolean => {
  if (environment.authorization_flow === "direct_credential" || isConfigurationPending(environment)) return false;

  const { status, credential_refresh: refresh } = environment;
  const canRetry = status === "awaiting_authorization" || status === "error";
  const canRecover = AUTHORIZATION_RECOVERY_STATUSES.has(status);
  const fallbackAction = fallbackAuthorizationAction(environment, Date.now());
  if (fallbackAction === "blocked") return false;
  if (fallbackAction === "recover") return canRetry || canRecover;
  if (fallbackAction === "retry") return canRetry;
  const refreshRejected = status !== "ready" && refresh?.schedule_reason === "reauthorization_required";
  return canRetry || (canRecover && refreshRejected);
};
