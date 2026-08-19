// 本文件把解析器原始值和有效值转换为可比较、可人工修正的稳定字段行。

import type { ActiveOverride, JsonValue, OverrideTarget, ParsedChannelData } from "./types";

export interface ParserFieldRow {
  path: string;
  label: string;
  rawValue: JsonValue | undefined;
  effectiveValue: JsonValue | undefined;
  changed: boolean;
  activeOverrideId: string | null;
  target: OverrideTarget;
}

const ROOT_FIELDS = ["metered", "billing_routes", "capabilities", "unresolved_fields", "evidence", "warnings"] as const;
const SUBSCRIPTION_FIELDS = [
  "plan_id",
  "plan_name",
  "status",
  "starts_at",
  "expires_at",
  "models",
  "balance",
  "currency",
  "channel_concurrency",
  "model_concurrency",
  "limits",
] as const;

const LABELS: Record<string, string> = {
  subscription: "套餐",
  plan_id: "套餐 ID",
  plan_name: "套餐名称",
  status: "套餐状态",
  starts_at: "开始时间",
  expires_at: "到期时间",
  models: "套餐模型",
  balance: "套餐余额",
  currency: "币种",
  channel_concurrency: "渠道并发",
  model_concurrency: "模型并发",
  limits: "额度窗口",
  metered: "按量分组与价格",
  billing_routes: "可执行计费路由",
  capabilities: "渠道能力",
  unresolved_fields: "未解析字段",
  evidence: "解析证据",
  warnings: "警告",
};

const isJsonObject = (value: JsonValue | undefined): value is { [key: string]: JsonValue } =>
  value !== null && typeof value === "object" && !Array.isArray(value);

const canonicalize = (value: JsonValue | undefined): string => {
  if (value === undefined) return "undefined";
  if (Array.isArray(value)) return `[${value.map(canonicalize).join(",")}]`;
  if (isJsonObject(value)) {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalize(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
};

interface RowInput {
  path: string;
  field: string;
  rawValue: JsonValue | undefined;
  effectiveValue: JsonValue | undefined;
  target: OverrideTarget;
}

const row = (input: RowInput, overrides: ReadonlyMap<string, string>): ParserFieldRow => ({
  path: input.path,
  label: LABELS[input.field] ?? input.field,
  rawValue: input.rawValue,
  effectiveValue: input.effectiveValue,
  changed: canonicalize(input.rawValue) !== canonicalize(input.effectiveValue),
  activeOverrideId: overrides.get(input.path) ?? null,
  target: input.target,
});

export const buildParserFieldRows = (
  raw: ParsedChannelData,
  effective: ParsedChannelData,
  activeOverrides: ActiveOverride[],
): ParserFieldRow[] => {
  const overrides = new Map(activeOverrides.map((override) => [override.field_path, override.override_id]));
  const rawSubscription = raw.subscription;
  const effectiveSubscription = effective.subscription;
  const subscriptionRows =
    isJsonObject(rawSubscription) || isJsonObject(effectiveSubscription)
      ? SUBSCRIPTION_FIELDS.map((field) =>
          row(
            {
              path: `/subscription/${field}`,
              field,
              rawValue: isJsonObject(rawSubscription) ? rawSubscription[field] : undefined,
              effectiveValue: isJsonObject(effectiveSubscription) ? effectiveSubscription[field] : undefined,
              target: { kind: "subscription_field", field },
            },
            overrides,
          ),
        )
      : [
          row(
            {
              path: "/subscription",
              field: "subscription",
              rawValue: rawSubscription,
              effectiveValue: effectiveSubscription,
              target: { kind: "root_field", field: "subscription" },
            },
            overrides,
          ),
        ];
  const rootRows = ROOT_FIELDS.map((field) =>
    row(
      {
        path: `/${field}`,
        field,
        rawValue: raw[field],
        effectiveValue: effective[field],
        target: { kind: "root_field", field },
      },
      overrides,
    ),
  );
  return [...subscriptionRows, ...rootRows];
};

export const formatJsonValue = (value: JsonValue | undefined): string =>
  value === undefined ? "null" : JSON.stringify(value, null, 2);
