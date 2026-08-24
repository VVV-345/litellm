import type { JsonDecimal, JsonValue } from "./types";

export interface MeteredPriceDraft {
  providerModelId: string;
  groupId: string | null;
  groupName: string | null;
  currency: string;
  unit: string;
  inputPrice: string;
  outputPrice: string;
  cacheReadPrice: string;
  cacheWritePrice: string;
  groupMultiplier: string;
}

interface MeteredPriceValue {
  provider_model_id?: JsonValue;
  currency?: JsonValue;
  unit?: JsonValue;
  input_price?: JsonValue;
  output_price?: JsonValue;
  cache_read_price?: JsonValue;
  cache_write_price?: JsonValue;
  group_multiplier?: JsonValue;
  price_calculation?: JsonValue;
  effective_prices?: JsonValue;
}

interface MeteredGroupValue {
  group_id?: JsonValue;
  group_name?: JsonValue;
  models?: JsonValue;
}

const asObject = (value: JsonValue | undefined): Record<string, JsonValue> | null =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value : null;

const asString = (value: JsonValue | undefined, fallback = ""): string => (typeof value === "string" ? value : fallback);

const decimalText = (value: JsonValue | undefined, fallback = ""): string =>
  typeof value === "string" || typeof value === "number" ? String(value) : fallback;

const priceDraft = (value: JsonValue, group: MeteredGroupValue): MeteredPriceDraft | null => {
  const price = asObject(value) as MeteredPriceValue | null;
  if (!price || typeof price.provider_model_id !== "string") return null;
  return {
    providerModelId: price.provider_model_id,
    groupId: typeof group.group_id === "string" ? group.group_id : null,
    groupName: typeof group.group_name === "string" ? group.group_name : null,
    currency: asString(price.currency, "RATIO"),
    unit: asString(price.unit, "multiplier"),
    inputPrice: decimalText(price.input_price),
    outputPrice: decimalText(price.output_price),
    cacheReadPrice: decimalText(price.cache_read_price),
    cacheWritePrice: decimalText(price.cache_write_price),
    groupMultiplier: decimalText(price.group_multiplier, "1"),
  };
};

export const buildMeteredPriceDrafts = (value: JsonValue | undefined, models: string[] = []): MeteredPriceDraft[] => {
  const metered = asObject(value);
  const parsed = !metered || !Array.isArray(metered.groups)
    ? []
    : metered.groups.flatMap((value) => {
        const group = asObject(value) as MeteredGroupValue | null;
        if (!group || !Array.isArray(group.models)) return [];
        return group.models.map((price) => priceDraft(price, group)).filter((draft): draft is MeteredPriceDraft => draft !== null);
      });
  return parsed.length
    ? parsed
    : models.map((providerModelId) => ({
        providerModelId,
        groupId: "manual",
        groupName: "manual",
        currency: "RATIO",
        unit: "multiplier",
        inputPrice: "",
        outputPrice: "",
        cacheReadPrice: "",
        cacheWritePrice: "",
        groupMultiplier: "1",
      }));
};

const parseDecimal = (value: string, field: string, allowEmpty: boolean): JsonDecimal | null => {
  const trimmed = value.trim();
  if (!trimmed && allowEmpty) return null;
  if (!/^\d+(?:\.\d+)?$/.test(trimmed)) throw new Error(`${field} 必须是非负十进制数`);
  return trimmed;
};

const effectivePrice = (source: JsonDecimal | null, multiplier: JsonDecimal): JsonDecimal | null => {
  if (source === null) return null;
  return String(Number(source) * Number(multiplier));
};

const priceValue = (draft: MeteredPriceDraft): JsonValue => {
  const multiplier = parseDecimal(draft.groupMultiplier, "分组倍率", false);
  if (multiplier === null || Number(multiplier) <= 0) throw new Error("分组倍率必须大于零");
  const inputPrice = parseDecimal(draft.inputPrice, "输入价格", true);
  const outputPrice = parseDecimal(draft.outputPrice, "输出价格", true);
  const cacheReadPrice = parseDecimal(draft.cacheReadPrice, "缓存读取价格", true);
  const cacheWritePrice = parseDecimal(draft.cacheWritePrice, "缓存写入价格", true);
  return {
    provider_model_id: draft.providerModelId,
    currency: draft.currency.trim() || "RATIO",
    unit: draft.unit.trim() || "multiplier",
    input_price: inputPrice,
    output_price: outputPrice,
    cache_read_price: cacheReadPrice,
    cache_write_price: cacheWritePrice,
    group_multiplier: multiplier,
    price_calculation: "multiplier",
    effective_prices: {
      input_price: effectivePrice(inputPrice, multiplier),
      output_price: effectivePrice(outputPrice, multiplier),
      cache_read_price: effectivePrice(cacheReadPrice, multiplier),
      cache_write_price: effectivePrice(cacheWritePrice, multiplier),
    },
    normalized_per_million_tokens: null,
    conversion_note: null,
    litellm_model_name: null,
    public_model_name: null,
    concurrency: null,
  };
};

export const buildMeteredOverrideValue = (drafts: MeteredPriceDraft[]): JsonValue => {
  if (!drafts.length) throw new Error("至少填写一个模型价格");
  const groupIds = [...new Set(drafts.map((draft) => draft.groupId ?? "manual"))];
  return {
    groups: groupIds.map((groupId) => {
      const groupDrafts = drafts.filter((draft) => (draft.groupId ?? "manual") === groupId);
      return {
        group_id: groupId,
        group_name: groupDrafts[0]?.groupName ?? groupId,
        models: groupDrafts.map(priceValue),
        concurrency: null,
      };
    }),
  };
};
