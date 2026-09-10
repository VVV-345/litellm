import { describe, expect, it } from "vitest";

import type { PolicyView } from "./AccountPoolManagementApi";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";
import { filterAccountPoolEnvironments, summarizeAccountPoolEnvironments } from "./accountPoolSelectors";

const environment = {
  id: "00000000-0000-4000-8000-000000000001",
  name: "Primary",
  status: "ready",
  updated_at: "2026-09-10T00:00:00Z",
} as AccountPoolEnvironment;

const policy = {
  card_id: environment.id,
  policy: { group: "Production", tags: ["premium"] },
} as PolicyView;

describe("filterAccountPoolEnvironments", () => {
  it.each(["production", "PREMIUM"])("matches policy metadata using %s", (search) => {
    expect(filterAccountPoolEnvironments([environment], search, "all", [policy])).toEqual([environment]);
  });
});

describe("summarizeAccountPoolEnvironments", () => {
  it("summarizes routable and lifecycle states without treating flags as extra accounts", () => {
    const environments = [
      { ...environment, id: "ready-enabled", enabled: true },
      { ...environment, id: "ready-disabled", enabled: false },
      { ...environment, id: "awaiting", status: "awaiting_authorization", enabled: true },
      { ...environment, id: "automatic-cooldown", status: "cooling_down", manual_cooldown: false, enabled: false },
      { ...environment, id: "manual-cooldown", status: "cooling_down", manual_cooldown: true, enabled: true },
      { ...environment, id: "disabled-manual", status: "disabled", manual_cooldown: true, enabled: false },
      { ...environment, id: "error", status: "error", enabled: true },
    ] as AccountPoolEnvironment[];
    const expectedOverview = {
      total: 7,
      ready: 1,
      awaitingAuthorization: 1,
      coolingDown: 2,
      error: 1,
    };

    expect(summarizeAccountPoolEnvironments(environments)).toEqual(expectedOverview);
  });
});
