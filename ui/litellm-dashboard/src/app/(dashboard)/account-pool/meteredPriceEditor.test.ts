import { describe, expect, it } from "vitest";

import { buildMeteredOverrideValue, buildMeteredPriceDrafts } from "./meteredPriceEditor";

describe("metered price editor", () => {
  it("prepopulates an editable draft from parsed multiplier pricing", () => {
    expect(
      buildMeteredPriceDrafts(
        {
          groups: [
            {
              group_id: "premium",
              models: [
                {
                  provider_model_id: "model-a",
                  currency: "RATIO",
                  unit: "multiplier",
                  input_price: "2",
                  output_price: "3",
                  group_multiplier: "1.5",
                },
              ],
            },
          ],
        },
        ["model-a"],
      ),
    ).toEqual([
      {
        providerModelId: "model-a",
        groupId: "premium",
        groupName: null,
        currency: "RATIO",
        unit: "multiplier",
        inputPrice: "2",
        outputPrice: "3",
        cacheReadPrice: "",
        cacheWritePrice: "",
        groupMultiplier: "1.5",
      },
    ]);
  });

  it("creates editable drafts from discovered models when pricing is absent", () => {
    expect(buildMeteredPriceDrafts(null, ["model-a"])).toEqual([
      {
        providerModelId: "model-a",
        groupId: "manual",
        groupName: "manual",
        currency: "USD",
        unit: "million_tokens",
        inputPrice: "",
        outputPrice: "",
        cacheReadPrice: "",
        cacheWritePrice: "",
        groupMultiplier: "1",
      },
    ]);
  });

  it("builds validated metered data with effective prices", () => {
    expect(
      buildMeteredOverrideValue([
        {
          providerModelId: "model-a",
          groupId: "manual",
          groupName: "manual",
          currency: "RATIO",
          unit: "multiplier",
          inputPrice: "2",
          outputPrice: "3",
          cacheReadPrice: "",
          cacheWritePrice: "",
          groupMultiplier: "1.5",
        },
      ]),
    ).toMatchObject({
      groups: [
        {
          group_id: "manual",
          models: [
            {
              provider_model_id: "model-a",
              input_price: "2",
              output_price: "3",
              group_multiplier: "1.5",
              effective_prices: { input_price: "3", output_price: "4.5" },
            },
          ],
        },
      ],
    });
  });

  it("rejects a non-positive group multiplier", () => {
    expect(() =>
      buildMeteredOverrideValue([
        {
          providerModelId: "model-a",
          groupId: "manual",
          groupName: "manual",
          currency: "RATIO",
          unit: "multiplier",
          inputPrice: "2",
          outputPrice: "3",
          cacheReadPrice: "",
          cacheWritePrice: "",
          groupMultiplier: "0",
        },
      ]),
    ).toThrow("分组倍率必须大于零");
  });
});
