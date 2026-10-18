import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { dashboardBackgroundTasks } from "./dashboardBackground";

const context = {
  queryClient: new QueryClient(),
  accessToken: "test-token",
  userId: "test-user",
  userRole: "admin",
};

describe("dashboard background preload", () => {
  it("skips disabled modules and their initial queries", () => {
    const tasks = dashboardBackgroundTasks(context, new AbortController().signal, [
      "llm-playground",
      "agents",
      "api_ref",
      "usage",
    ]);
    const ids = tasks.map((task) => task.id);

    expect(ids).not.toContain("page:playground");
    expect(ids).not.toContain("page:agents");
    expect(ids).not.toContain("page:api-reference");
    expect(ids).not.toContain("page:old-usage");
    expect(ids).toContain("page:guardrails");
    expect(ids).toContain("page:guardrails-monitor");
  });

  it("keeps every preload task when nothing is disabled", () => {
    const all = dashboardBackgroundTasks(context, new AbortController().signal);
    const filtered = dashboardBackgroundTasks(context, new AbortController().signal, ["agents", "guardrails"]);

    expect(all.length).toBeGreaterThan(filtered.length);
    expect(all.map((task) => task.id)).toContain("page:agents");
    expect(filtered.map((task) => task.id)).not.toContain("page:agents");
  });
});
