import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/http/client";

import { createAccountPoolEnvironment, deleteAccountPoolEnvironment } from "./AccountPoolApi";
import type { AccountPoolCreateRequest } from "./AccountPoolTypes";

const postMock = vi.fn();
const deleteMock = vi.fn();

vi.mock("uuid", () => ({ v4: () => "operation-123" }));

vi.mock("@/components/networking", () => ({
  apiClient: {
    delete: (...args: unknown[]) => deleteMock(...args),
    post: (...args: unknown[]) => postMock(...args),
  },
}));

describe("createAccountPoolEnvironment", () => {
  beforeEach(() => {
    deleteMock.mockReset();
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
    const [path, options] = postMock.mock.calls[0] as [
      string,
      { accessToken: string; body: object; headers: Record<string, string> },
    ];
    expect(path).toBe("/account_pool/environments");
    expect(options.accessToken).toBe("token-123");
    expect(options.body).toEqual(requestBody);
    expect(options.headers).toEqual({ "Idempotency-Key": "operation-123" });
    expect(JSON.stringify(options.body)).not.toMatch(/image|command|callback|api_base|api_key|secret/i);
  });

  it("retries a gateway failure once with the same idempotency key", async () => {
    const requestBody: AccountPoolCreateRequest = {
      name: "Claude account",
      provider: "openai",
      channel: "cliproxyapi",
      supplier: "anthropic_claude",
    };
    postMock.mockRejectedValueOnce(new ApiError("Bad Gateway", 502)).mockResolvedValueOnce({ id: "created" });

    await expect(createAccountPoolEnvironment("token-123", requestBody)).resolves.toEqual({ id: "created" });

    expect(postMock).toHaveBeenCalledTimes(2);
    expect(postMock.mock.calls[0][1]).toMatchObject({ headers: { "Idempotency-Key": "operation-123" } });
    expect(postMock.mock.calls[1][1]).toMatchObject({ headers: { "Idempotency-Key": "operation-123" } });
  });

  it("retries deletion safely after a gateway failure", async () => {
    deleteMock.mockRejectedValueOnce(new ApiError("Gateway Timeout", 504)).mockResolvedValueOnce(undefined);

    await expect(deleteAccountPoolEnvironment("token-123", "environment-1")).resolves.toBeUndefined();

    expect(deleteMock).toHaveBeenCalledTimes(2);
    expect(deleteMock.mock.calls[0][1]).toMatchObject({ headers: { "Idempotency-Key": "operation-123" } });
    expect(deleteMock.mock.calls[1][1]).toMatchObject({ headers: { "Idempotency-Key": "operation-123" } });
  });
});
