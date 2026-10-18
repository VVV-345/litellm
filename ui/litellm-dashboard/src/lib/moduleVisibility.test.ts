import { describe, expect, it } from "vitest";

import {
  DEFAULT_DISABLED_PAGES,
  MODULE_OPTIONS,
  canonicalPageKey,
  disabledPagesOrDefault,
  isPageDisabled,
  setPageEnabled,
} from "./moduleVisibility";

describe("module visibility", () => {
  it("keeps every configurable module in the default disabled list", () => {
    expect(new Set(MODULE_OPTIONS.map((option) => option.page))).toEqual(new Set(DEFAULT_DISABLED_PAGES));
  });

  it("maps warmup route names to sidebar page keys", () => {
    expect(canonicalPageKey("playground")).toBe("llm-playground");
    expect(canonicalPageKey("api-reference")).toBe("api_ref");
    expect(canonicalPageKey("old-usage")).toBe("usage");
    expect(canonicalPageKey("agents")).toBe("agents");
  });

  it("disables configured modules and keeps settings accessible", () => {
    expect(isPageDisabled("playground", DEFAULT_DISABLED_PAGES)).toBe(true);
    expect(isPageDisabled("llm-playground", DEFAULT_DISABLED_PAGES)).toBe(true);
    expect(isPageDisabled("api-keys", DEFAULT_DISABLED_PAGES)).toBe(false);
    expect(isPageDisabled("settings", ["settings"])).toBe(false);
  });

  it("enables and disables a single page without touching the rest", () => {
    expect(setPageEnabled(["agents", "mcp-servers"], "agents", true)).toEqual(["mcp-servers"]);
    expect(setPageEnabled(["agents"], "guardrails", false)).toEqual(["agents", "guardrails"]);
    const unchanged = ["agents"];
    expect(setPageEnabled(unchanged, "agents", false)).toBe(unchanged);
  });

  it("falls back to the default list when the persisted value is invalid", () => {
    expect(disabledPagesOrDefault(undefined)).toBe(DEFAULT_DISABLED_PAGES);
    expect(disabledPagesOrDefault(["agents", 3])).toBe(DEFAULT_DISABLED_PAGES);
    expect(disabledPagesOrDefault(["agents"])).toEqual(["agents"]);
  });
});
