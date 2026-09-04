import { beforeEach, describe, expect, it, vi } from "vitest";

import { createAccountPoolEnvironment } from "./AccountPoolApi";

const postMock = vi.fn();

vi.mock("@/components/networking", () => ({
  apiClient: {
    post: (...args: unknown[]) => postMock(...args),
  },
}));

describe("createAccountPoolEnvironment", () => {
  beforeEach(() => {
    postMock.mockReset();
    postMock.mockResolvedValue({});
  });

  it("sends the selected channel and supplier with no extra fields", async () => {
    await createAccountPoolEnvironment("token-123", {
      name: "Claude account",
      provider: "openai",
      channel: "cliproxyapi",
      supplier: "anthropic_claude",
    });

    expect(postMock).toHaveBeenCalledTimes(1);
    const [path, options] = postMock.mock.calls[0] as [string, { accessToken: string; body: object }];
    expect(path).toBe("/account_pool/environments");
    expect(options.accessToken).toBe("token-123");
    expect(options.body).toEqual({
      name: "Claude account",
      provider: "openai",
      channel: "cliproxyapi",
      supplier: "anthropic_claude",
    });
    expect(JSON.stringify(options.body)).not.toMatch(/image|command|callback|api_base|api_key|secret/i);
  });
});
