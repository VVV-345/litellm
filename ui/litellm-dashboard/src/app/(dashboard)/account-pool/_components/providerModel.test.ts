import { describe, expect, it } from "vitest";

import { normalizeForwardingProvider, providerModelForSelectedModel } from "./providerModel";

describe("providerModelForSelectedModel", () => {
  it("normalizes legacy channel service IDs for the forwarding selector", () => {
    expect(normalizeForwardingProvider("openai_compatible")).toBe("openai");
    expect(normalizeForwardingProvider("anthropic")).toBe("anthropic");
  });

  it("adds the OpenAI prefix to an unqualified model", () => {
    expect(providerModelForSelectedModel("gpt-4o", "openai")).toBe("openai/gpt-4o");
  });

  it("adds the ZAI prefix to an unqualified model", () => {
    expect(providerModelForSelectedModel("glm-4.7", "zai")).toBe("zai/glm-4.7");
  });

  it("keeps a slash-qualified upstream model beneath the selected forwarding protocol", () => {
    expect(providerModelForSelectedModel("azure/eu/gpt-5.6-sol", "zai")).toBe("zai/azure/eu/gpt-5.6-sol");
  });

  it("keeps a multi-segment upstream model beneath the selected forwarding protocol", () => {
    expect(providerModelForSelectedModel("openai/responses/gpt-5.6", "zai")).toBe("zai/openai/responses/gpt-5.6");
  });
});
