// 本文件校验套餐人工录入草稿，并生成可保存的统一套餐覆盖数据。

import { asJsonObject, decimalText, parseRequiredNonNegativeDecimal } from "./parserOverrideEditor";
import type { JsonValue } from "./types";

export interface SubscriptionDraft {
  planName: string;
  selectedModels: string[];
  remainingUsage: string;
  usageUnit: string;
  channelConcurrency: string;
}

interface SubscriptionValue {
  plan_name?: JsonValue;
  models?: JsonValue;
  balance?: JsonValue;
  currency?: JsonValue;
  channel_concurrency?: JsonValue;
}

const selectedProviderModelIds = (value: JsonValue | undefined, allowedModels: readonly string[]): string[] => {
  if (!Array.isArray(value)) return [...allowedModels];
  const allowed = new Set(allowedModels);
  const selected = value.flatMap((item) => {
    const model = asJsonObject(item);
    const providerModelId = model?.provider_model_id;
    return typeof providerModelId === "string" && allowed.has(providerModelId) ? [providerModelId] : [];
  });
  return selected.length === 0 ? [...allowedModels] : [...new Set(selected)];
};

export const buildSubscriptionDraft = (value: JsonValue, allowedModels: readonly string[]): SubscriptionDraft => {
  const subscription = asJsonObject(value) as SubscriptionValue | null;
  return {
    planName: typeof subscription?.plan_name === "string" ? subscription.plan_name : "",
    selectedModels: selectedProviderModelIds(subscription?.models, allowedModels),
    remainingUsage: decimalText(subscription?.balance),
    usageUnit: typeof subscription?.currency === "string" ? subscription.currency : "次",
    channelConcurrency: decimalText(subscription?.channel_concurrency, "10"),
  };
};

const parseChannelConcurrency = (value: string): number => {
  const trimmed = value.trim();
  if (!trimmed) return 10;
  if (!/^\d+$/.test(trimmed) || Number(trimmed) < 1) throw new Error("渠道并发必须是大于零的整数");
  return Number(trimmed);
};

export const buildSubscriptionOverrideValue = (
  draft: SubscriptionDraft,
  allowedModels: readonly string[],
): JsonValue => {
  const allowed = new Set(allowedModels.map((model) => model.trim()).filter(Boolean));
  const selected = [...new Set(draft.selectedModels.map((model) => model.trim()).filter(Boolean))];
  if (selected.length === 0) throw new Error("至少选择一个套餐覆盖模型");
  if (selected.some((model) => !allowed.has(model))) throw new Error("只能选择本次解析发现的模型");
  const usageUnit = draft.usageUnit.trim();
  if (!usageUnit) throw new Error("请填写剩余用量单位");
  return {
    plan_id: null,
    plan_name: draft.planName.trim() || null,
    status: "active",
    starts_at: null,
    expires_at: null,
    models: selected.map((providerModelId) => ({
      provider_model_id: providerModelId,
      litellm_model_name: null,
      public_model_name: null,
    })),
    balance: parseRequiredNonNegativeDecimal(draft.remainingUsage, "剩余用量"),
    currency: usageUnit,
    channel_concurrency: parseChannelConcurrency(draft.channelConcurrency),
    model_concurrency: [],
    limits: [],
  };
};
