/** 本文件计算号池仪表盘的聚合指标与供应商分组，数据只来自已加载的卡片和请求统计。 */

import type { ErrorStats } from "./AccountPoolManagementApi";
import type { AccountPoolEnvironment, AccountPoolSupplier } from "./AccountPoolTypes";

export interface AccountPoolDashboardSummary {
  totalCards: number;
  enabledCards: number;
  successfulRequests: number;
  failedRequests: number;
  totalRequests: number;
  totalTokens: number;
  successRate: number | null;
}

export interface AccountPoolSupplierGroup {
  supplier: AccountPoolSupplier;
  environments: AccountPoolEnvironment[];
}

export const summarizeAccountPoolDashboard = (
  environments: readonly AccountPoolEnvironment[],
  statsByCard: ReadonlyMap<string, ErrorStats>,
): AccountPoolDashboardSummary => {
  const totals = environments.reduce(
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
    enabledCards: environments.filter((environment) => environment.enabled && !environment.manual_cooldown).length,
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
  environment.enabled_models.slice(0, 3);

export const accountPoolHiddenModelCount = (environment: AccountPoolEnvironment): number =>
  Math.max(0, environment.enabled_models.length - accountPoolVisibleModels(environment).length);
