/** 验证人民币输入精度与跨月续费日期。 */
import { describe, expect, it } from "vitest";
import { expiryLabel, nextDate, parseCents } from "./costForm";

describe("manual cost values", () => {
  it.each([
    ["0.29", 29],
    ["199", 19900],
    [" 12.3 ", 1230],
    ["10000000000", 1_000_000_000_000],
  ])("converts %s into exact cents", (input, expected) => expect(parseCents(input)).toBe(expected));
  it.each(["", "0", "-1", "1.005", "1e3", "Infinity", "10000000000.01", "1,000"])(
    "rejects invalid amount %s",
    (input) => expect(parseCents(input)).toBeNull(),
  );
  it("advances inclusive service periods across leap days and years", () => {
    expect(nextDate("2024-02-28")).toBe("2024-02-29");
    expect(nextDate("2024-02-29")).toBe("2024-03-01");
    expect(nextDate("2026-12-31")).toBe("2027-01-01");
    expect(expiryLabel(null)).toBe("未录入");
    expect(expiryLabel(0)).toBe("今天到期");
    expect(expiryLabel(-2)).toBe("已到期 2 天");
  });
});
