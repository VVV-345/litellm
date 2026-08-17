import { ComplexityTiers } from "./ComplexityRouterConfig";

export type AutoRouterTestMode = "chat" | "embedding";

export interface AutoRouterTestTarget {
  labels: string[];
  modelGroup: string;
  mode: AutoRouterTestMode;
}

export interface BuildAutoRouterTestTargetsParams {
  tiers: ComplexityTiers;
  semanticMatchingEnabled: boolean;
  embeddingModel: string | undefined;
}

// Keys drive iteration order; `satisfies Record<keyof ComplexityTiers, null>` makes it a
// compile error to add a tier to ComplexityTiers without listing it here (and vice versa).
const TIER_ORDER = Object.keys({
  SIMPLE: null,
  MEDIUM: null,
  COMPLEX: null,
  REASONING: null,
} satisfies Record<keyof ComplexityTiers, null>) as (keyof ComplexityTiers)[];

export const buildAutoRouterTestTargets = (
  {
    tiers,
    semanticMatchingEnabled,
    embeddingModel,
  }: BuildAutoRouterTestTargetsParams,
  t: (key: string, opts?: { defaultValue?: string }) => string = (key: string) => key.replace(/^ui\./, ""),
): AutoRouterTestTarget[] => {
  const groupedByModel = TIER_ORDER.reduce<Record<string, string[]>>((acc, tier) => {
    return (tiers[tier] ?? []).reduce((tierAcc, rawModel) => {
      const modelGroup = rawModel?.trim();
      if (!modelGroup) return tierAcc;
      return { ...tierAcc, [modelGroup]: [...(tierAcc[modelGroup] ?? []), tier] };
    }, acc);
  }, {});

  const tierTargets: AutoRouterTestTarget[] = Object.entries(groupedByModel).map(([modelGroup, labels]) => ({
    labels,
    modelGroup,
    mode: "chat" as const,
  }));

  const embeddingTarget: AutoRouterTestTarget[] =
    semanticMatchingEnabled && embeddingModel?.trim()
      ? [{ labels: [t("ui.Embedding")], modelGroup: embeddingModel.trim(), mode: "embedding" as const }]
      : [];

  return [...tierTargets, ...embeddingTarget];
};
