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
