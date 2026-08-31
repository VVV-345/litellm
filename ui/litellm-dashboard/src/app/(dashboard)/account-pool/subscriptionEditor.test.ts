// 本文件验证套餐人工录入只覆盖已发现模型，并默认使用渠道并发十。

import { describe, expect, it } from "vitest";

import { buildSubscriptionDraft, buildSubscriptionOverrideValue } from "./subscriptionEditor";

describe("subscription editor", () => {
  it("selects all discovered models for a new manual package and defaults concurrency to ten", () => {
    expect(buildSubscriptionDraft(null, ["model-a", "model-b"])).toEqual({
      planName: "",
      selectedModels: ["model-a", "model-b"],
      remainingUsage: "",
      usageUnit: "次",
      channelConcurrency: "10",
    });
  });

  it("preserves only discovered models from an existing package", () => {
    expect(
      buildSubscriptionDraft(
        {
          plan_name: "套餐 A",
          models: [{ provider_model_id: "model-a" }, { provider_model_id: "stale-model" }],
          balance: "50",
          currency: "积分",
          channel_concurrency: 6,
        },
        ["model-a", "model-b"],
      ),
    ).toEqual({
      planName: "套餐 A",
      selectedModels: ["model-a"],
      remainingUsage: "50",
      usageUnit: "积分",
      channelConcurrency: "6",
    });
  });

  it("builds an active package with a shared channel concurrency cap", () => {
    expect(
      buildSubscriptionOverrideValue(
        {
          planName: "测试套餐",
          selectedModels: ["model-a"],
          remainingUsage: "25.5",
          usageUnit: "次",
          channelConcurrency: "",
        },
        ["model-a", "model-b"],
      ),
    ).toMatchObject({
      plan_name: "测试套餐",
      status: "active",
      models: [{ provider_model_id: "model-a" }],
      balance: "25.5",
      currency: "次",
      channel_concurrency: 10,
    });
  });

  it("rejects models that were not discovered for this channel", () => {
    expect(() =>
      buildSubscriptionOverrideValue(
        {
          planName: "",
          selectedModels: ["other-model"],
          remainingUsage: "1",
          usageUnit: "次",
          channelConcurrency: "10",
        },
        ["model-a"],
      ),
    ).toThrow("只能选择本次解析发现的模型");
  });
});
