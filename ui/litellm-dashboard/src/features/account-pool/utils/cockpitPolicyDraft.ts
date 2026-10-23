/** 转换 Cockpit 策略草稿并校验可保存字段，不修补已保存的无效值。 */
import type { CockpitPolicyUpdate, CockpitPolicyView, CockpitRoutingRule } from "../api/AccountPoolManagementApi";

type RuleDraft = Omit<CockpitRoutingRule, "priority" | "weight"> & { priority: string; weight: string };
export type CockpitPolicyDraft = { revision: string; routing_strategy: string; custom_rules: RuleDraft[] };

export function cockpitPolicyDraft(policy: CockpitPolicyView): CockpitPolicyDraft {
  return {
    revision: policy.revision,
    routing_strategy: policy.routing_strategy,
    custom_rules: policy.account_ids.map((account_id) => {
      const rule = policy.custom_rules.find((item) => item.account_id === account_id);
      return {
        account_id,
        priority: String(rule?.priority ?? 0),
        weight: String(rule?.weight ?? 1),
        is_backup: rule?.is_backup ?? false,
        is_preferred: rule?.is_preferred ?? false,
      };
    }),
  };
}

export function cockpitPolicyUpdate(
  draft: CockpitPolicyDraft,
  supported: CockpitPolicyView["supported_strategies"],
): CockpitPolicyUpdate | null {
  const strategy = supported.find((value) => value === draft.routing_strategy);
  if (
    !strategy ||
    draft.custom_rules.some((rule) => {
      const priority = Number(rule.priority);
      const weight = Number(rule.weight);
      const validPriority = rule.priority.trim() !== "" && Number.isInteger(priority) && Math.abs(priority) <= 10000;
      const validWeight = Number.isInteger(weight) && weight >= 1 && weight <= 10000;
      const validRole = !(rule.is_backup && rule.is_preferred);
      return !(validPriority && validWeight && validRole);
    })
  )
    return null;
  return {
    revision: draft.revision,
    routing_strategy: strategy,
    custom_rules: draft.custom_rules.map((rule) => ({
      ...rule,
      priority: Number(rule.priority),
      weight: Number(rule.weight),
    })),
  };
}
