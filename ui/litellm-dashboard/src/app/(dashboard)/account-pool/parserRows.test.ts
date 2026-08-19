// 本文件验证解析字段差异、稳定覆盖路径和 JSON 展示转换。

import { describe, expect, it } from "vitest";

import { buildParserFieldRows, formatJsonValue } from "./parserRows";
import type { ParsedChannelData } from "./types";

const parsedData = (subscription: ParsedChannelData["subscription"]): ParsedChannelData => ({
  subscription,
  metered: null,
  billing_routes: [],
  capabilities: [],
  unresolved_fields: [],
  evidence: [],
  warnings: [],
});

describe("buildParserFieldRows", () => {
  it("uses stable subscription paths and marks active changes", () => {
    const rows = buildParserFieldRows(
      parsedData({ plan_name: "Basic", balance: "5" }),
      parsedData({ balance: "9", plan_name: "Basic" }),
      [
        {
          override_id: "override-1",
          field_path: "/subscription/balance",
          source_parser_run_id: "run-1",
          occurred_at: "2026-08-19T00:00:00Z",
        },
      ],
    );

    const balance = rows.find((candidate) => candidate.path === "/subscription/balance");
    const planName = rows.find((candidate) => candidate.path === "/subscription/plan_name");

    expect(balance).toMatchObject({
      changed: true,
      activeOverrideId: "override-1",
      target: { kind: "subscription_field", field: "balance" },
    });
    expect(planName?.changed).toBe(false);
  });

  it("compares object keys independently of response order", () => {
    const rows = buildParserFieldRows(
      { ...parsedData(null), metered: { group_name: "default", group_id: "group-1" } },
      { ...parsedData(null), metered: { group_id: "group-1", group_name: "default" } },
      [],
    );

    expect(rows.find((candidate) => candidate.path === "/metered")?.changed).toBe(false);
    expect(formatJsonValue(undefined)).toBe("null");
  });
});
