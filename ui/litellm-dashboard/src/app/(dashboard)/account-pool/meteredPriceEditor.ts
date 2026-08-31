// 本文件将已发现模型的人工价格草稿校验并转换为解析覆盖数据。

import {
  asJsonObject,
  asString,
  decimalText,
  parseOptionalNonNegativeDecimal,
  parseRequiredNonNegativeDecimal,
} from "./parserOverrideEditor";
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

const priceDraft = (value: JsonValue, group: MeteredGroupValue): MeteredPriceDraft | null => {
  const price = asJsonObject(value) as MeteredPriceValue | null;
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

const emptyDraft = (providerModelId: string): MeteredPriceDraft => ({
  providerModelId,
  groupId: "manual",
  groupName: "manual",
  currency: "USD",
  unit: "million_tokens",
  inputPrice: "",
  outputPrice: "",
  cacheReadPrice: "",
  cacheWritePrice: "",
  groupMultiplier: "1",
});

export const buildMeteredPriceDrafts = (value: JsonValue | undefined, models: string[] = []): MeteredPriceDraft[] => {
  const metered = asJsonObject(value);
  const parsed =
    !metered || !Array.isArray(metered.groups)
      ? []
      : metered.groups.flatMap((value) => {
          const group = asJsonObject(value) as MeteredGroupValue | null;
          if (!group || !Array.isArray(group.models)) return [];
          return group.models
            .map((price) => priceDraft(price, group))
            .filter((draft): draft is MeteredPriceDraft => draft !== null);
        });
  const parsedByModel = new Map(parsed.map((draft) => [draft.providerModelId, draft]));
  const discoveredModels = Array.from(new Set(models.map((model) => model.trim()).filter(Boolean)));
  return discoveredModels.map((providerModelId) => parsedByModel.get(providerModelId) ?? emptyDraft(providerModelId));
};

const effectivePrice = (source: JsonDecimal | null, multiplier: JsonDecimal): JsonDecimal | null => {
  if (source === null) return null;
  return String(Number(source) * Number(multiplier));
};

const priceValue = (draft: MeteredPriceDraft): JsonValue => {
  const multiplier = parseRequiredNonNegativeDecimal(draft.groupMultiplier, "分组倍率");
  if (Number(multiplier) <= 0) throw new Error("分组倍率必须大于零");
  const inputPrice = parseRequiredNonNegativeDecimal(draft.inputPrice, "输入价格");
  const outputPrice = parseRequiredNonNegativeDecimal(draft.outputPrice, "输出价格");
  const cacheReadPrice = parseOptionalNonNegativeDecimal(draft.cacheReadPrice, "缓存读取价格");
  const cacheWritePrice = parseOptionalNonNegativeDecimal(draft.cacheWritePrice, "缓存写入价格");
  return {
    provider_model_id: draft.providerModelId.trim(),
    currency: draft.currency.trim() || "USD",
    unit: draft.unit.trim() || "million_tokens",
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
  };
};

export const buildMeteredOverrideValue = (
  drafts: MeteredPriceDraft[],
  allowedModels?: readonly string[],
): JsonValue => {
  const pricedDrafts = drafts.filter((draft) => draft.inputPrice.trim() || draft.outputPrice.trim());
  if (!pricedDrafts.length) throw new Error("至少填写一个模型价格");
  const permittedModels = allowedModels === undefined ? null : new Set(allowedModels.map((model) => model.trim()));
  const modelIds = pricedDrafts.map((draft) => draft.providerModelId.trim());
  if (modelIds.some((modelId) => !modelId)) throw new Error("模型名称不能为空");
  if (new Set(modelIds).size !== modelIds.length) throw new Error("模型名称不能重复");
  if (permittedModels !== null && modelIds.some((modelId) => !permittedModels.has(modelId))) {
    throw new Error("只能补充本次解析发现的模型");
  }
  const groupIds = [...new Set(pricedDrafts.map((draft) => draft.groupId ?? "manual"))];
  return {
    groups: groupIds.map((groupId) => {
      const groupDrafts = pricedDrafts.filter((draft) => (draft.groupId ?? "manual") === groupId);
      return {
        group_id: groupId,
        group_name: groupDrafts[0]?.groupName ?? groupId,
        models: groupDrafts.map(priceValue),
      };
    }),
  };
};
