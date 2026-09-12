import type { PolicyView } from "./AccountPoolManagementApi";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";

export interface AccountPoolPolicyOption {
  label: string;
  value: string;
  description?: string;
}

export interface AccountPoolPolicyOptions {
  groups: string[];
  tags: AccountPoolPolicyOption[];
  models: AccountPoolPolicyOption[];
  accounts: AccountPoolPolicyOption[];
  codexAppServerClients: AccountPoolPolicyOption[];
  antigravitySensitiveWords: AccountPoolPolicyOption[];
}

const uniqueSorted = (values: readonly string[]): string[] =>
  Array.from(new Set(values.map((value) => value.trim()).filter(Boolean))).sort((left, right) =>
    left.localeCompare(right),
  );

export const buildAccountPoolPolicyOptions = (
  current: AccountPoolEnvironment,
  environments: readonly AccountPoolEnvironment[],
  policies: readonly PolicyView[],
): AccountPoolPolicyOptions => {
  const policyValues = policies.flatMap((policy) => (policy.policy ? [policy.policy] : []));
  const relatedEnvironments = environments.filter(
    (environment) => environment.channel === current.channel && environment.supplier === current.supplier,
  );
  const relatedEnvironmentIds = new Set(relatedEnvironments.map((environment) => environment.id));
  const relatedPolicyValues = policies.flatMap((policy) =>
    relatedEnvironmentIds.has(policy.card_id) && policy.policy ? [policy.policy] : [],
  );
  const models = uniqueSorted([
    ...relatedEnvironments.flatMap((environment) => [...environment.available_models, ...environment.enabled_models]),
    ...relatedPolicyValues.flatMap((policy) => policy.excluded_models),
    ...relatedPolicyValues.flatMap((policy) => policy.model_aliases.flatMap((alias) => [alias.alias, alias.target])),
  ]);

  return {
    groups: uniqueSorted(policyValues.map((policy) => policy.group)),
    tags: uniqueSorted(policyValues.flatMap((policy) => policy.tags)).map((value) => ({ label: value, value })),
    models: models.map((value) => ({ label: value, value })),
    accounts: relatedEnvironments
      .filter((environment) => environment.id !== current.id)
      .map((environment) => ({
        label: environment.name,
        value: environment.id,
        description: environment.id,
      })),
    codexAppServerClients: uniqueSorted(
      policyValues.flatMap((policy) => policy.codex?.allow_app_server_clients ?? []),
    ).map((value) => ({ label: value, value })),
    antigravitySensitiveWords: uniqueSorted(
      policyValues.flatMap((policy) => policy.antigravity?.sensitive_words ?? []),
    ).map((value) => ({ label: value, value })),
  };
};
