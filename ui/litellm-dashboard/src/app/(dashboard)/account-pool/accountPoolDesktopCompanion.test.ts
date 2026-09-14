import { describe, expect, it } from "vitest";

import { buildDesktopCompanionUrl, desktopStatusSchema } from "./accountPoolDesktopCompanion";

describe("accountPoolDesktopCompanion", () => {
  it("puts the secret in the custom-scheme fragment", () => {
    const url = buildDesktopCompanionUrl("https://redstars.ai/ui/login", {
      ticket_id: "123e4567-e89b-12d3-a456-426614174000",
      secret: "s".repeat(43),
      expires_at: "2026-09-14T00:05:00Z",
    });

    expect(url).toContain("server=https%3A%2F%2Fredstars.ai");
    expect(url).toContain("ticket_id=123e4567-e89b-12d3-a456-426614174000");
    expect(url.endsWith(`#${"s".repeat(43)}`)).toBe(true);
    expect(url.split("#")[0]).not.toContain("s".repeat(43));
  });

  it("rejects remote plain HTTP origins", () => {
    expect(() =>
      buildDesktopCompanionUrl("http://example.com", {
        ticket_id: "123e4567-e89b-12d3-a456-426614174000",
        secret: "s".repeat(43),
        expires_at: "2026-09-14T00:05:00Z",
      }),
    ).toThrow(/HTTPS/);
  });

  it("rejects malformed desktop status payloads", () => {
    expect(() =>
      desktopStatusSchema.parse({ codex_instances: [{ id: "one" }], cursor_instances: [], codex_wsl: {} }),
    ).toThrow();
  });
});
