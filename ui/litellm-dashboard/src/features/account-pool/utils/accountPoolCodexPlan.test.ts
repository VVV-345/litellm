import { describe, expect, it } from "vitest";

import { accountPoolPlanLabel } from "./accountPoolCodexPlan";

describe("accountPoolPlanLabel", () => {
  it("shows the Codex Pro multiplier used by Cockpit", () => {
    expect(accountPoolPlanLabel("pro", "prolite")).toBe("PRO 5x");
    expect(accountPoolPlanLabel("pro", null)).toBe("PRO 20x");
  });

  it("normalizes standard subscription tiers", () => {
    expect(accountPoolPlanLabel("self_serve_business_usage_based", null)).toBe("BUSINESS");
    expect(accountPoolPlanLabel("plus", null)).toBe("PLUS");
    expect(accountPoolPlanLabel(null, null)).toBe("-");
  });
});
