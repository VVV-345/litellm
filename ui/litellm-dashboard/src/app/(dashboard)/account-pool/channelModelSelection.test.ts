import { describe, expect, it } from "vitest";

import type { ChannelBindingInput, UpstreamModelDiscoveryResult, UpstreamProviderManifest } from "./types";
import {
  buildDiscoveredBindings,
  canSubmitCreateSelection,
  initialModelSelection,
  selectManualMapping,
  validateDiscoveryResult,
} from "./channelModelSelection";

const provider: UpstreamProviderManifest = {
  provider_id: "openrouter",
  display_name: "OpenRouter",
  default_api_base: "https://openrouter.ai/api/v1",
};

const discovery: UpstreamModelDiscoveryResult = {
  ok: true,
  provider_id: provider.provider_id,
  normalized_api_base: provider.default_api_base,
  message: "已获取模型",
  failure_code: null,
  models: ["openai/gpt-5.6", "model-a", "model-a"],
};

describe("channel model selection", () => {
  it("requires an upstream vendor before automatic discovery", () => {
    expect(initialModelSelection(undefined)).toMatchObject({ kind: "manual-required", reason: "missing" });
    expect(initialModelSelection(provider)).toEqual({ kind: "ready-to-validate" });
  });

  it("keeps upstream model IDs separate from the selected LiteLLM forwarding protocol", () => {
    const selection = validateDiscoveryResult(initialModelSelection(provider), discovery);

    expect(selection).toEqual({
      kind: "discovered",
      models: ["model-a", "openai/gpt-5.6"],
    });
    expect(selection.kind).toBe("discovered");
    if (selection.kind !== "discovered") return;
    expect(buildDiscoveredBindings(selection.models, "openrouter")).toEqual([
      expect.objectContaining({ public_model: "model-a", provider_model: "openrouter/model-a" }),
      expect.objectContaining({ public_model: "openai/gpt-5.6", provider_model: "openrouter/openai/gpt-5.6" }),
    ]);
  });

  it("does not permit a changed discovered mapping to be submitted", () => {
    const selection = validateDiscoveryResult(initialModelSelection(provider), discovery);
    const alteredBinding: ChannelBindingInput = {
      binding_id: null,
      public_model: "model-a",
      provider_model: "openai/model-a",
      litellm_deployment_id: null,
      ownership: "pool_managed",
      enabled: true,
    };

    expect(canSubmitCreateSelection(selection, [alteredBinding], "openrouter")).toBe(false);
  });

  it("requires an explicit manual mapping after discovery fails", () => {
    const failed = validateDiscoveryResult(initialModelSelection(provider), {
      ...discovery,
      ok: false,
      failure_code: "authentication",
      message: "API Key 无效",
      models: [],
    });
    const manual = selectManualMapping(failed);
    const binding: ChannelBindingInput = {
      binding_id: null,
      public_model: "alias",
      provider_model: "openai/upstream-model",
      litellm_deployment_id: null,
      ownership: "pool_managed",
      enabled: true,
    };

    expect(canSubmitCreateSelection(failed, [binding], "openai")).toBe(false);
    expect(canSubmitCreateSelection(manual, [binding], "openai")).toBe(true);
  });
});
