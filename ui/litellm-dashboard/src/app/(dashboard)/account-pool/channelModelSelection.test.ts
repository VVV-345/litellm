import { describe, expect, it } from "vitest";

import type { ChannelBindingInput, ProviderCapability, ProviderServiceManifest, ProviderValidationResult } from "./types";
import {
  buildDiscoveredBindings,
  canSubmitCreateSelection,
  initialModelSelection,
  selectManualMapping,
  validateDiscoveryResult,
} from "./channelModelSelection";

const supported: ProviderCapability = {
  capability: "model_discovery",
  state: "supported",
  message: "Models can be discovered",
};

const provider = (capabilities: ProviderCapability[]): ProviderServiceManifest => ({
  provider_id: "openai_compatible",
  display_name: "Compatible API",
  default_api_base: "https://gateway.example/v1",
  litellm_provider_prefix: "openai",
  capabilities,
});

const validation = (overrides: Partial<ProviderValidationResult> = {}): ProviderValidationResult => ({
  ok: true,
  provider_id: "openai_compatible",
  normalized_api_base: "https://gateway.example/v1",
  group: null,
  key_fingerprint: null,
  message: "Validated",
  failure_code: null,
  models: [{ model: "z-model" }, { model: "vendor/a-model" }, { model: "z-model" }],
  ...overrides,
});

describe("channel model selection", () => {
  it.each<ProviderCapability["state"] | "missing">([
    "unsupported",
    "unavailable",
    "missing",
  ])("requires an explicit manual transition when model discovery is %s", (state) => {
    const capabilities = state === "missing" ? [] : [{ ...supported, state }];

    expect(initialModelSelection(provider(capabilities))).toMatchObject({ kind: "manual-required" });
  });

  it("requires validation for supported model discovery", () => {
    expect(initialModelSelection(provider([supported]))).toEqual({ kind: "ready-to-validate" });
  });

  it("sorts and deduplicates exact upstream IDs after discovery", () => {
    const selection = validateDiscoveryResult(initialModelSelection(provider([supported])), validation());

    expect(selection).toEqual({ kind: "discovered", models: ["vendor/a-model", "z-model"] });
  });

  it("turns an empty successful response into an explicit manual fallback", () => {
    const selection = validateDiscoveryResult(
      initialModelSelection(provider([supported])),
      validation({ models: [] }),
    );

    expect(selection).toMatchObject({ kind: "manual-required", reason: "empty-models" });
  });

  it("turns a failed validation response into an explicit manual fallback", () => {
    const selection = validateDiscoveryResult(
      initialModelSelection(provider([supported])),
      validation({ ok: false, failure_code: "connection_failed", models: [] }),
    );

    expect(selection).toMatchObject({ kind: "manual-required", reason: "validation-failed" });
  });

  it("retains the backend-safe failure message and code", () => {
    const selection = validateDiscoveryResult(
      initialModelSelection(provider([supported])),
      validation({
        ok: false,
        failure_code: "authentication_failed",
        message: "The upstream rejected this credential",
        models: [],
      }),
    );

    expect(selection).toMatchObject({
      kind: "manual-required",
      reason: "validation-failed",
      message: "The upstream rejected this credential",
      failureCode: "authentication_failed",
    });
  });

  it("only permits manual bindings after the explicit transition", () => {
    const selection = initialModelSelection(provider([{ ...supported, state: "unavailable" }]));
    const manualSelection = selectManualMapping(selection);
    const binding: ChannelBindingInput = {
      binding_id: null,
      public_model: "alias",
      provider_model: "openai/upstream",
      litellm_deployment_id: null,
      ownership: "pool_managed",
      enabled: true,
    };

    expect(canSubmitCreateSelection(selection, [binding])).toBe(false);
    expect(manualSelection).toEqual({ kind: "manual" });
    expect(canSubmitCreateSelection(manualSelection, [binding])).toBe(true);
  });

  it("requires discovered bindings to be selected exact upstream IDs", () => {
    const selection = validateDiscoveryResult(initialModelSelection(provider([supported])), validation());
    const validBindings = buildDiscoveredBindings(["vendor/a-model", "z-model"], "openai");
    const invalidBindings = buildDiscoveredBindings(["not-returned"], "openai");

    expect(validBindings).toEqual([
      expect.objectContaining({ public_model: "vendor/a-model", provider_model: "vendor/a-model" }),
      expect.objectContaining({ public_model: "z-model", provider_model: "openai/z-model" }),
    ]);
    expect(canSubmitCreateSelection(selection, validBindings)).toBe(true);
    expect(canSubmitCreateSelection(selection, invalidBindings)).toBe(false);
  });
});
