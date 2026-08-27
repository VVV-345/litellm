import type { ChannelBindingInput, ProviderServiceManifest, ProviderValidationResult } from "./types";
import { providerModelForSelectedModel } from "./_components/providerModel";

export type CreateModelSelection =
  | { kind: "ready-to-validate" }
  | { kind: "discovered"; models: string[] }
  | {
      kind: "manual-required";
      reason: "unsupported" | "unavailable" | "missing" | "validation-failed" | "empty-models";
      message?: string;
      failureCode?: string;
    }
  | { kind: "manual" };

const modelDiscoveryCapability = (provider: ProviderServiceManifest) =>
  provider.capabilities.find((capability) => capability.capability === "model_discovery");

export const initialModelSelection = (provider: ProviderServiceManifest | undefined): CreateModelSelection => {
  const capability = provider && modelDiscoveryCapability(provider);
  if (!capability) return { kind: "manual-required", reason: "missing" };
  if (capability.state === "supported") return { kind: "ready-to-validate" };
  return { kind: "manual-required", reason: capability.state };
};

export const validateDiscoveryResult = (
  selection: CreateModelSelection,
  result: ProviderValidationResult,
): CreateModelSelection => {
  if (selection.kind === "manual") return selection;
  if (!result.ok) {
    return {
      kind: "manual-required",
      reason: "validation-failed",
      message: result.message,
      failureCode: result.failure_code ?? undefined,
    };
  }
  const models = Array.from(new Set(result.models.map((model) => model.model))).sort((left, right) =>
    left.localeCompare(right),
  );
  return models.length > 0 ? { kind: "discovered", models } : { kind: "manual-required", reason: "empty-models" };
};

export const selectManualMapping = (selection: CreateModelSelection): CreateModelSelection =>
  selection.kind === "manual-required" || selection.kind === "discovered" ? { kind: "manual" } : selection;

export const buildDiscoveredBindings = (models: string[], providerPrefix: string): ChannelBindingInput[] =>
  models.map((model) => ({
    binding_id: null,
    public_model: model,
    provider_model: providerModelForSelectedModel(model, providerPrefix),
    litellm_deployment_id: null,
    ownership: "pool_managed",
    enabled: true,
  }));

const bindingIsValid = (binding: ChannelBindingInput): boolean =>
  Boolean(binding.public_model.trim() && (binding.provider_model?.trim() || binding.litellm_deployment_id));

export const canSubmitCreateSelection = (selection: CreateModelSelection, bindings: ChannelBindingInput[]): boolean => {
  if (selection.kind === "manual") return bindings.length > 0 && bindings.every(bindingIsValid);
  if (selection.kind !== "discovered") return false;
  const discoveredModels = new Set(selection.models);
  return bindings.length > 0 && bindings.every((binding) => discoveredModels.has(binding.public_model));
};
