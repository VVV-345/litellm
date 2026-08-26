import { describe, expect, it, vi } from "vitest";

import { generateRequestUuid } from "./uuid";

describe("generateRequestUuid", () => {
  it("uses the native UUID generator when available", () => {
    const randomUUID = vi.fn(() => "123e4567-e89b-42d3-a456-426614174000");

    expect(generateRequestUuid({ randomUUID })).toBe("123e4567-e89b-42d3-a456-426614174000");
    expect(randomUUID).toHaveBeenCalledOnce();
  });

  it("uses Web Crypto random bytes when randomUUID is unavailable", () => {
    const getRandomValues = vi.fn((values: Uint8Array) => {
      values.fill(0);
      return values;
    });

    expect(generateRequestUuid({ getRandomValues } as Crypto)).toBe("00000000-0000-4000-8000-000000000000");
    expect(getRandomValues).toHaveBeenCalledOnce();
  });

  it("generates an RFC 4122 v4 UUID without Web Crypto", () => {
    const result = generateRequestUuid(undefined, () => 0.5);

    expect(result).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });
});
