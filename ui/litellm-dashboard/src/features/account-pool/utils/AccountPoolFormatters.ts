/** 本文件提供号池状态、额度和时间的纯格式化逻辑，供卡片与配置弹窗复用。 */

import type {
  AccountPoolEnvironment,
  AccountPoolQuotaSnapshot,
  AccountPoolQuotaWindow,
  AccountPoolStatus,
} from "./AccountPoolTypes";
import type { TFunction } from "i18next";

export const concurrencyLimitLabel = (t: TFunction): string => t("accountPool.config.concurrencyLimit");

export const statusLabel = (t: TFunction, status: AccountPoolStatus): string => t(`accountPool.status.${status}`);

export const statusVariant = (status: AccountPoolStatus): "default" | "secondary" | "destructive" | "outline" => {
  if (status === "ready") return "default";
  if (status === "error") return "destructive";
  if (status === "cooling_down" || status === "disabled") return "secondary";
  return "outline";
};

export const mostConstrainedWindow = (environment: AccountPoolEnvironment): AccountPoolQuotaWindow | null => {
  const windows = [...environment.quota.windows, ...environment.model_quotas.flatMap((item) => item.quota.windows)];
  if (windows.length === 0) return null;
  return windows.reduce((lowest, window) => (window.remaining_percent < lowest.remaining_percent ? window : lowest));
};

export const formatQuota = (t: TFunction, window: AccountPoolQuotaWindow | null): string =>
  window === null
    ? t("accountPool.config.notObserved")
    : `${window.remaining_percent.toFixed(window.remaining_percent % 1 ? 1 : 0)}%`;

export const quotaProxyLabel = (t: TFunction, environment: AccountPoolEnvironment): string => {
  if (environment.proxy_mode === "default_gateway") return t("accountPool.config.defaultGateway");
  const port = environment.proxy_profile_id?.match(/^clash-gateway-(\d+)$/)?.[1];
  return port
    ? t("accountPool.quotas.proxyPort", { port })
    : environment.proxy_profile_id || t("accountPool.dashboard.unknown");
};

export const formatQuotaAmounts = (t: TFunction, window: AccountPoolQuotaWindow, locale: string): string | null => {
  const formatter = new Intl.NumberFormat(locale, { maximumFractionDigits: 2 });
  const units: Record<string, string> = {
    cents: t("accountPool.quotas.unit.cents"),
    credits: t("accountPool.quotas.unit.credits"),
    tasks: t("accountPool.quotas.unit.tasks"),
  };
  const unit = window.unit ? ` ${units[window.unit] ?? window.unit}` : "";
  if (window.used != null && window.total != null)
    return `${formatter.format(window.used)} / ${formatter.format(window.total)}${unit}`;
  if (window.remaining != null) return `${formatter.format(window.remaining)}${unit}`;
  return null;
};

export const quotaWindowLabel = (t: TFunction, name: string): string => {
  const labels: Record<string, string> = {
    "5 hour": t("accountPool.quotas.window.fiveHour"),
    Weekly: t("accountPool.quotas.window.weekly"),
    "Code review 5 hour": t("accountPool.quotas.window.codeReviewFiveHour"),
    "Code review Weekly": t("accountPool.quotas.window.codeReviewWeekly"),
    "7 day": t("accountPool.quotas.window.sevenDay"),
    "7 day Sonnet": t("accountPool.quotas.window.sevenDaySonnet"),
    "7 day Fable": t("accountPool.quotas.window.sevenDayFable"),
    "Extra usage": t("accountPool.quotas.window.extraUsage"),
    Monthly: t("accountPool.quotas.window.monthly"),
    "On-demand": t("accountPool.quotas.window.onDemand"),
    "Tasks: Frequent": t("accountPool.quotas.window.frequentTasks"),
    "Tasks: Occasional": t("accountPool.quotas.window.occasionalTasks"),
    Model: t("accountPool.quotas.window.model"),
    "Quota bucket": t("accountPool.quotas.window.quotaBucket"),
  };
  if (labels[name]) return labels[name];
  if (name.startsWith("Product: "))
    return t("accountPool.quotas.window.product", { product: name.slice("Product: ".length) });
  if (name.endsWith(" 5 hour"))
    return t("accountPool.quotas.window.featureFiveHour", { feature: name.slice(0, -" 5 hour".length) });
  if (name.endsWith(" Weekly"))
    return t("accountPool.quotas.window.featureWeekly", { feature: name.slice(0, -" Weekly".length) });
  return name;
};

export const formatDateTime = (value: string | null | undefined, locale: string): string => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat(locale, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
};

export const quotaRows = (
  t: TFunction,
  environment: AccountPoolEnvironment,
): Array<{ key: string; label: string; quota: AccountPoolQuotaSnapshot }> => [
  { key: "account", label: t("accountPool.config.account"), quota: environment.quota },
  ...environment.model_quotas.map((item) => ({ key: item.model, label: item.model, quota: item.quota })),
];
