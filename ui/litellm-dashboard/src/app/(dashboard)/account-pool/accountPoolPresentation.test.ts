import { describe, expect, it } from "vitest";

import {
  administrativeStatePresentation,
  channelPriorityPresentation,
  formatAccountPoolDateTime,
  formatAccountPoolEpoch,
  formatAccountPoolNumber,
  healthPresentation,
  parseOptionalNumber,
} from "./accountPoolPresentation";

describe("accountPoolPresentation", () => {
  it("formats supported numeric and temporal values with stable fallbacks", () => {
    expect(formatAccountPoolNumber("12.3456")).toBe("12.35");
    expect(formatAccountPoolNumber("invalid")).toBe("未知");
    expect(formatAccountPoolDateTime(null)).toBe("暂无");
    expect(formatAccountPoolDateTime("invalid", "不可用")).toBe("不可用");
    expect(formatAccountPoolEpoch(null)).toBe("-");
  });

  it("keeps the canonical state presentations and optional number parsing", () => {
    expect(administrativeStatePresentation.paused.label).toBe("暂停");
    expect(channelPriorityPresentation[400].label).toBe("最高");
    expect(healthPresentation.cooldown.label).toBe("冷却中");
    expect(parseOptionalNumber("  ")).toBeNull();
    expect(parseOptionalNumber("2.5")).toBe(2.5);
  });
});
