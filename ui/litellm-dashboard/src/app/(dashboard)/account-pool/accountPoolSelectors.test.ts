import { describe, expect, it } from "vitest";

import type { PolicyView } from "./AccountPoolManagementApi";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";
import { filterAccountPoolEnvironments } from "./accountPoolSelectors";

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
