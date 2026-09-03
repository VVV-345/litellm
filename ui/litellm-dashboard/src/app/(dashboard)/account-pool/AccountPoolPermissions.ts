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

const isConfigurationPending = (environment: AccountPoolEnvironment): boolean =>
  environment.configuration_pending === true;

export const canToggleEnvironment = (environment: AccountPoolEnvironment): boolean =>
  !isConfigurationPending(environment) &&
  !TRANSITIONAL_STATUSES.has(environment.status) &&
  !(environment.status === "error" && environment.available_models.length === 0);

export const canConfigureEnvironment = (environment: AccountPoolEnvironment): boolean =>
  !isConfigurationPending(environment) &&
  !TRANSITIONAL_STATUSES.has(environment.status) &&
  !(environment.status === "error" && environment.available_models.length === 0);

export const canDeleteEnvironment = (environment: AccountPoolEnvironment): boolean =>
  !isConfigurationPending(environment) && environment.status !== "deleting";

export const canAuthorizeEnvironment = (environment: AccountPoolEnvironment): boolean =>
  !isConfigurationPending(environment) &&
  (environment.status === "awaiting_authorization" || environment.status === "error");
