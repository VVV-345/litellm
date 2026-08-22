import { describe, expect, it } from "vitest";

import { providerModelForSelectedModel } from "./providerModel";

describe("providerModelForSelectedModel", () => {
  it("adds the OpenAI prefix to an unqualified model", () => {
    expect(providerModelForSelectedModel("gpt-4o", "openai")).toBe("openai/gpt-4o");
  });

  it("adds the ZAI prefix to an unqualified model", () => {
    expect(providerModelForSelectedModel("glm-4.7", "zai")).toBe("zai/glm-4.7");
  });

  it("preserves a selected model that already has a provider prefix", () => {
    expect(providerModelForSelectedModel("azure/eu/gpt-5.6-sol", "zai")).toBe("azure/eu/gpt-5.6-sol");
  });

  it("preserves a multi-segment selected model identifier", () => {
    expect(providerModelForSelectedModel("openai/responses/gpt-5.6", "zai")).toBe("openai/responses/gpt-5.6");
  });
});
