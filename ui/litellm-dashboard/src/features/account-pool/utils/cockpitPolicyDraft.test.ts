import { expect, it } from "vitest";
import type { CockpitPolicyView } from "../api/AccountPoolManagementApi";
import { cockpitPolicyDraft, cockpitPolicyUpdate } from "./cockpitPolicyDraft";

const policy: CockpitPolicyView = {
  revision: '"sha256-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
  routing_strategy: "auto",
  custom_rules: [],
  account_ids: ["account-a"],
  editable: true,
  blocked_reason: null,
  scope: "codex_pool_global",
  runtime_activation: "restart_required_or_unverified",
  supported_strategies: ["auto", "custom"],
  unsupported_fields: [],
};

it("materializes sparse rules using Cockpit defaults without changing the read revision", () => {
  expect(cockpitPolicyUpdate(cockpitPolicyDraft(policy), policy.supported_strategies)).toEqual({
    revision: '"sha256-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
    routing_strategy: "auto",
    custom_rules: [{ account_id: "account-a", priority: 0, weight: 1, is_backup: false, is_preferred: false }],
  });
});

it.each([
  { priority: "" },
  { priority: "1.5" },
  { priority: "-10001" },
  { priority: "10001" },
  { weight: "" },
  { weight: "0" },
  { weight: "10001" },
  { weight: "1.5" },
  { is_backup: true, is_preferred: true },
])("rejects invalid rule %j rather than silently repairing it", (invalid) => {
  const draft = cockpitPolicyDraft(policy);
  expect(
    cockpitPolicyUpdate(
      { ...draft, custom_rules: [{ ...draft.custom_rules[0], ...invalid }] },
      policy.supported_strategies,
    ),
  ).toBeNull();
});

it("does not convert unsupported saved strategies to auto", () => {
  const draft = cockpitPolicyDraft({ ...policy, routing_strategy: "unknown" });
  expect(draft.routing_strategy).toBe("unknown");
  expect(cockpitPolicyUpdate(draft, policy.supported_strategies)).toBeNull();
});

it("keeps invalid existing weights visible rather than applying defaults", () => {
  const draft = cockpitPolicyDraft({
    ...policy,
    custom_rules: [{ account_id: "account-a", priority: 3, weight: 0, is_backup: false, is_preferred: false }],
  });
  expect(draft.custom_rules[0].weight).toBe("0");
  expect(cockpitPolicyUpdate(draft, policy.supported_strategies)).toBeNull();
});
