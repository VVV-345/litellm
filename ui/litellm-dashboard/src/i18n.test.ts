import { describe, expect, it } from "vitest";

import en from "./locales/en.json";
import zhCN from "./locales/zh-CN.json";

const keysOf = (value: Record<string, unknown>, prefix = ""): string[] =>
  Object.entries(value).flatMap(([key, nestedValue]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    return typeof nestedValue === "object" && nestedValue !== null
      ? keysOf(nestedValue as Record<string, unknown>, path)
      : [path];
  });

describe("locales", () => {
  it("provides the same translation keys in English and Simplified Chinese", () => {
    expect(keysOf(zhCN).sort()).toEqual(keysOf(en).sort());
  });
});
