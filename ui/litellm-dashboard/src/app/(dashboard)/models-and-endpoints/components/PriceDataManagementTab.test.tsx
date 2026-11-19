/* @vitest-environment jsdom */
import { fireEvent, render, screen, within } from "@testing-library/react";
import i18n from "@/i18n";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PriceDataManagementTab from "./PriceDataManagementTab";

vi.mock("@/components/price_data_reload", () => ({ default: () => <div>reload</div> }));
vi.mock("@/app/(dashboard)/hooks/useAuthorized", () => ({ default: () => ({ accessToken: "sk-test" }) }));
vi.mock("@/app/(dashboard)/hooks/models/useModelCostMap", () => ({
  useModelCostMap: () => ({
    refetch: vi.fn(),
    data: {
      "zai/glm-5.3": {
        litellm_provider: "zai",
        input_cost_per_token: 0.0000014,
        cache_read_input_token_cost: 0.00000026,
        output_cost_per_token: 0.0000044,
      },
      "public-a": { litellm_provider: "openai", base_model: "openai/foreign-fixture", input_cost_per_token: 0 },
      "public-b": { litellm_provider: "openai", base_model: "openai/foreign-fixture", input_cost_per_token: 0 },
      "deployment-a": { id: "deployment-a", base_model: "openai/foreign-fixture", input_cost_per_token: 0.000013 },
      "deployment-b": { id: "deployment-b", base_model: "openai/foreign-fixture", input_cost_per_token: 0.000013 },
      "deployment-c": {
        id: "deployment-c",
        litellm_provider: "openai",
        base_model: "openai/foreign-fixture",
        input_cost_per_token: 0.000013,
        input_cost_per_token_above_128k_tokens: 0.000015,
      },
    },
  }),
}));

describe("PriceDataManagementTab", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en");
  });
  it("renders its content standalone, without a tab-panel ancestor", () => {
    const { getByText } = render(<PriceDataManagementTab />);
    expect(getByText("Price Data Management")).toBeInTheDocument();
  });

  it("keeps GLM markets and tiers separate and groups only equivalent aliases or deployments", () => {
    render(<PriceDataManagementTab initialSearch="glm-5.3" />);
    const cn = screen.getByText("bigmodel.cn/glm-5.3").closest("tr")!;
    expect(within(cn).getByText("CNY / 1M")).toBeVisible();
    expect(within(cn).getByText("28")).toBeVisible();
    const international = screen.getByText("zai/glm-5.3").closest("tr")!;
    expect(within(international).getByText("USD / 1M")).toBeVisible();
    expect(within(international).getByText("4.4")).toBeVisible();
    const search = screen.getByRole("textbox");
    fireEvent.change(search, { target: { value: "bigmodel.cn/glm-5.1" } });
    expect(screen.getAllByRole("row")).toHaveLength(3);
    fireEvent.change(search, { target: { value: "foreign-fixture" } });
    expect(screen.getAllByRole("row")).toHaveLength(4);
    expect(screen.getByText("public-a, public-b")).toBeVisible();
    expect(screen.getByText("deployment-a, deployment-b")).toBeVisible();
    expect(screen.getAllByText("13")).toHaveLength(2);
    expect(screen.getByText("0")).toBeVisible();
    expect(screen.getAllByText("Unknown").length).toBeGreaterThan(0);
  });
});
