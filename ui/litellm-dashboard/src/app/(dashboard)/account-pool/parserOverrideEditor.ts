// 本文件复用人工解析补充表单的 JSON 读取和非负小数校验。

import type { JsonDecimal, JsonValue } from "./types";

export type JsonObject = Record<string, JsonValue>;

const decimalPattern = /^\d+(?:\.\d+)?$/;

export const asJsonObject = (value: JsonValue | undefined): JsonObject | null =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value : null;

export const asString = (value: JsonValue | undefined, fallback = ""): string =>
  typeof value === "string" ? value : fallback;

export const decimalText = (value: JsonValue | undefined, fallback = ""): string =>
  typeof value === "string" || typeof value === "number" ? String(value) : fallback;

export const parseRequiredNonNegativeDecimal = (value: string, field: string): JsonDecimal => {
  const trimmed = value.trim();
  if (!decimalPattern.test(trimmed)) throw new Error(`${field} 必须是非负十进制数`);
  return trimmed;
};

export const parseOptionalNonNegativeDecimal = (value: string, field: string): JsonDecimal | null => {
  const trimmed = value.trim();
  return trimmed ? parseRequiredNonNegativeDecimal(trimmed, field) : null;
};
