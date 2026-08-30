import type { ChannelBindingInput, UpstreamModelDiscoveryResult, UpstreamProviderManifest } from "./types";
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

export const initialModelSelection = (upstreamProvider: UpstreamProviderManifest | undefined): CreateModelSelection =>
  upstreamProvider ? { kind: "ready-to-validate" } : { kind: "manual-required", reason: "missing" };

export const validateDiscoveryResult = (
  selection: CreateModelSelection,
  result: UpstreamModelDiscoveryResult,
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
  const models = Array.from(new Set(result.models)).sort((left, right) => left.localeCompare(right));
  return models.length > 0 ? { kind: "discovered", models } : { kind: "manual-required", reason: "empty-models" };
};

export const selectManualMapping = (selection: CreateModelSelection): CreateModelSelection =>
  selection.kind === "manual-required" || selection.kind === "discovered" ? { kind: "manual" } : selection;

export const buildDiscoveredBindings = (models: string[], forwardingProvider: string): ChannelBindingInput[] =>
  models.map((model) => ({
    binding_id: null,
    public_model: model,
    provider_model: providerModelForSelectedModel(model, forwardingProvider),
    litellm_deployment_id: null,
    ownership: "pool_managed",
    enabled: true,
  }));

const bindingIsValid = (binding: ChannelBindingInput): boolean =>
  Boolean(binding.public_model.trim() && (binding.provider_model?.trim() || binding.litellm_deployment_id));

export const canSubmitCreateSelection = (
  selection: CreateModelSelection,
  bindings: ChannelBindingInput[],
  forwardingProvider: string,
): boolean => {
  if (selection.kind === "manual") return bindings.length > 0 && bindings.every(bindingIsValid);
  if (selection.kind !== "discovered") return false;
  const discoveredModels = new Set(selection.models);
  return (
    bindings.length > 0 &&
    bindings.every(
      (binding) =>
        discoveredModels.has(binding.public_model) &&
        binding.provider_model === providerModelForSelectedModel(binding.public_model, forwardingProvider) &&
        binding.public_model.trim().length > 0,
    )
  );
};
