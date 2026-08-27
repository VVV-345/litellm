// 本文件根据当前路由运行数据生成只读的自动排序建议，不会修改正式调度策略。

import type { RoutingTableEntry } from "./types";

export type AutoRankingSignal = "latency" | "quota" | "cost";

export interface AutoRankingPreviewEntry {
  route: RoutingTableEntry;
  position: number;
  score: number | null;
  signals: AutoRankingSignal[];
}

interface RankingInputs {
  latencyValues: number[];
  quotaValues: number[];
  costValues: number[];
  costBasis: string | null;
}

const stableRouteId = (route: RoutingTableEntry): string =>
  `${route.account_id}\u0000${route.deployment_id}\u0000${route.billing_route_id ?? ""}`;

const finiteNumber = (value: number | string | null): number | null => {
  if (value === null) return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
};

const sharedCostBasis = (routes: RoutingTableEntry[]): string | null => {
  const bases = Array.from(
    new Set(
      routes.flatMap((route) => {
        const evidence = route.cost_evidence;
        if (!evidence) return [];
        if (evidence.kind === "subscription_included") return [];
        if (!evidence.currency || !evidence.unit) return [];
        return [`${evidence.currency.toLowerCase()}\u0000${evidence.unit.toLowerCase()}`];
      }),
    ),
  );
  return bases.length === 1 ? bases[0] : null;
};

const routeCost = (route: RoutingTableEntry, basis: string | null): number | null => {
  const evidence = route.cost_evidence;
  if (!basis || !evidence) return null;
  if (evidence.kind === "subscription_included") return null;
  if (!evidence.currency || !evidence.unit) return null;
  const routeBasis = `${evidence.currency.toLowerCase()}\u0000${evidence.unit.toLowerCase()}`;
  return routeBasis === basis ? finiteNumber(route.effective_cost) : null;
};

const rangeScore = (value: number | null, values: number[], descending: boolean): number | null => {
  if (value === null || values.length === 0) return null;
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  if (minimum === maximum) return 100;
  const ratio = (value - minimum) / (maximum - minimum);
  return (descending ? ratio : 1 - ratio) * 100;
};

const previewEntry = (route: RoutingTableEntry, inputs: RankingInputs): AutoRankingPreviewEntry => {
  const latencyScore = rangeScore(route.latency_ewma_ms, inputs.latencyValues, false);
  const quotaScore = rangeScore(route.remaining_quota_ratio, inputs.quotaValues, true);
  const costScore = rangeScore(routeCost(route, inputs.costBasis), inputs.costValues, false);
  const signals: AutoRankingSignal[] = [
    ...(latencyScore === null ? [] : ["latency" as const]),
    ...(quotaScore === null ? [] : ["quota" as const]),
    ...(costScore === null ? [] : ["cost" as const]),
  ];
  const scoreValues = [latencyScore, quotaScore, costScore].filter((value): value is number => value !== null);
  return {
    route,
    position: 0,
    score:
      scoreValues.length === 0 ? null : scoreValues.reduce((total, value) => total + value, 0) / scoreValues.length,
    signals,
  };
};

export const buildAutoRankingPreview = (routes: RoutingTableEntry[]): AutoRankingPreviewEntry[] => {
  const available = routes.filter((route) => route.available);
  const costBasis = sharedCostBasis(available);
  // 只有币种和计价单位完全一致时才比较价格，避免把不可比成本排成高低。
  const latencyValues = available.flatMap((route) => (route.latency_ewma_ms === null ? [] : [route.latency_ewma_ms]));
  const quotaValues = available.flatMap((route) =>
    route.remaining_quota_ratio === null ? [] : [route.remaining_quota_ratio],
  );
  const costValues = available.flatMap((route) => {
    const value = routeCost(route, costBasis);
    return value === null ? [] : [value];
  });
  const inputs: RankingInputs = { latencyValues, quotaValues, costValues, costBasis };
  const preliminary = routes.map((route) => previewEntry(route, inputs));
  const ordered = [...preliminary].sort((left, right) => {
    if (left.route.available !== right.route.available) return left.route.available ? -1 : 1;
    if (left.score !== right.score) return (right.score ?? -1) - (left.score ?? -1);
    if (left.route.priority !== right.route.priority) return right.route.priority - left.route.priority;
    return stableRouteId(left.route).localeCompare(stableRouteId(right.route));
  });
  return ordered.map((entry, index) => ({ ...entry, position: index + 1 }));
};
