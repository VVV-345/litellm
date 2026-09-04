import { beforeEach, describe, expect, it, vi } from "vitest";

import { createAccountPoolEnvironment } from "./AccountPoolApi";
import type { AccountPoolCreateRequest } from "./AccountPoolTypes";

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
    const requestBody: AccountPoolCreateRequest = {
      name: "Claude account",
      provider: "openai",
      channel: "cliproxyapi",
      supplier: "anthropic_claude",
    };
    await createAccountPoolEnvironment("token-123", requestBody);

    expect(postMock).toHaveBeenCalledTimes(1);
    const [path, options] = postMock.mock.calls[0] as [string, { accessToken: string; body: object }];
    expect(path).toBe("/account_pool/environments");
    expect(options.accessToken).toBe("token-123");
    expect(options.body).toEqual(requestBody);
    expect(JSON.stringify(options.body)).not.toMatch(/image|command|callback|api_base|api_key|secret/i);
  });
});
