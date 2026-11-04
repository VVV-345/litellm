/** 本文件计算号池仪表盘的聚合指标与供应商分组，数据只来自已加载的卡片和请求统计。 */

import type { ErrorStats } from "../api/AccountPoolManagementApi";
import type { AccountPoolEnvironment, AccountPoolStatus, AccountPoolSupplier } from "./AccountPoolTypes";

export interface AccountPoolDashboardFilters {
  search: string;
  enabled: "all" | "enabled" | "disabled";
  status: "all" | AccountPoolStatus;
}

export const filterAccountPoolDashboardEnvironments = (
  environments: readonly AccountPoolEnvironment[],
  filters: AccountPoolDashboardFilters,
): AccountPoolEnvironment[] => {
  const search = filters.search.trim().toLocaleLowerCase();
  return environments.filter((environment) =>
    (filters.status === "all" || environment.status === filters.status) &&
    (filters.enabled === "all" || environment.enabled === (filters.enabled === "enabled")) &&
    (!search || [environment.name, environment.id].some((value) => value.toLocaleLowerCase().includes(search))),
  );
};

interface AccountPoolDashboardSummary {
  totalCards: number;
  enabledCards: number;
  successfulRequests: number;
  failedRequests: number;
  totalRequests: number;
  totalTokens: number;
  successRate: number | null;
}

interface AccountPoolSupplierGroup {
  supplier: AccountPoolSupplier;
  environments: AccountPoolEnvironment[];
}

export const summarizeAccountPoolDashboard = (
  environments: readonly AccountPoolEnvironment[],
  statsByCard: ReadonlyMap<string, ErrorStats>,
  standardSummary?: ErrorStats,
): AccountPoolDashboardSummary => {
  const totals = standardSummary
    ? {
        successfulRequests: standardSummary.succeeded_requests,
        failedRequests: standardSummary.failed_requests,
        totalRequests: standardSummary.total_requests,
        totalTokens: standardSummary.input_tokens + standardSummary.output_tokens,
      }
    : environments.reduce(
        (summary, environment) => {
          const stats = statsByCard.get(environment.id);
          if (!stats) return summary;
          return {
            successfulRequests: summary.successfulRequests + stats.succeeded_requests,
            failedRequests: summary.failedRequests + stats.failed_requests,
            totalRequests: summary.totalRequests + stats.total_requests,
            totalTokens: summary.totalTokens + stats.input_tokens + stats.output_tokens,
          };
        },
        { successfulRequests: 0, failedRequests: 0, totalRequests: 0, totalTokens: 0 },
      );
  const completedRequests = totals.successfulRequests + totals.failedRequests;
  return {
    totalCards: environments.length,
    enabledCards: environments.filter(
      (environment) =>
        environment.enabled &&
        !environment.manual_cooldown &&
        environment.status === "ready" &&
        !environment.configuration_pending &&
        accountPoolUsableModels(environment).length > 0,
    ).length,
    ...totals,
    successRate: completedRequests === 0 ? null : (totals.successfulRequests / completedRequests) * 100,
  };
};

export const groupAccountPoolEnvironments = (
  environments: readonly AccountPoolEnvironment[],
): AccountPoolSupplierGroup[] => {
  const groups = new Map<AccountPoolSupplier, AccountPoolEnvironment[]>();
  environments.forEach((environment) => {
    const group = groups.get(environment.supplier) ?? [];
    groups.set(environment.supplier, [...group, environment]);
  });
  return [...groups.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([supplier, groupedEnvironments]) => ({ supplier, environments: groupedEnvironments }));
};

export const accountPoolVisibleModels = (environment: AccountPoolEnvironment): readonly string[] =>
  accountPoolUsableModels(environment).slice(0, 3);

export const accountPoolHiddenModelCount = (environment: AccountPoolEnvironment): number =>
  Math.max(0, accountPoolUsableModels(environment).length - accountPoolVisibleModels(environment).length);

export const accountPoolUsableModels = (
  environment: Pick<AccountPoolEnvironment, "enabled_models"> &
    Partial<Pick<AccountPoolEnvironment, "access_token_fallback" | "model_cooldowns" | "quota" | "model_quotas" | "enabled" | "manual_cooldown" | "configuration_pending" | "status">>,
): readonly string[] => {
  const fallback = environment.access_token_fallback;
  if (fallback?.failure_source !== "refresh") return environment.enabled_models;
  if (environment.enabled === false || environment.manual_cooldown || environment.configuration_pending ||
      (environment.status !== undefined && environment.status !== "ready")) return [];
  const now = Date.now();
  if (!fallback.enabled || !fallback.active || !fallback.expires_at || Date.parse(fallback.expires_at) <= now) return [];
  return environment.enabled_models.filter((model) => {
    const modelQuota = environment.model_quotas?.find((item) => item.model === model && item.quota.windows.length > 0);
    const windows = modelQuota?.quota.windows ?? environment.quota?.windows ?? [];
    return fallback.verified_models?.includes(model) &&
      !environment.model_cooldowns?.some((cooldown) => cooldown.model === model && Date.parse(cooldown.retry_at) > now) &&
      !windows.some((window) => window.remaining_percent <= 0 && (!window.resets_at || Date.parse(window.resets_at) > now));
  });
};
