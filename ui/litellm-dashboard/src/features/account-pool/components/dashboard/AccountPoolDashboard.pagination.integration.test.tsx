import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import type { AccountPoolEnvironment } from "../../utils/AccountPoolTypes";
import { AccountPoolDashboard } from "./AccountPoolDashboard";

const card = (id: string, supplier: AccountPoolEnvironment["supplier"], changes: Partial<AccountPoolEnvironment> = {}): AccountPoolEnvironment => ({
  id, name: id, version: 1, provider: "openai", channel: "cliproxyapi", supplier,
  authorization_flow: "browser_oauth", status: "ready", enabled: true, manual_cooldown: false,
  configuration_pending: false, desired_configuration_version: 1, observed_configuration_version: 1,
  concurrency_limit: 2, proxy_mode: "default_gateway", proxy_profile_id: null,
  available_models: ["model"], enabled_models: ["model"], model_quotas: [],
  quota: { observed_at: null, plan_type: null, windows: [], balances: [] },
  cooldown_until: null, automatic_cooldown: false, last_error: null,
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", ...changes,
});
const cards = [
  ...Array.from({ length: 25 }, (_, index) => card(`Codex ${index + 1}`, "openai_codex")),
  ...Array.from({ length: 13 }, (_, index) => card(`Kimi ${index + 1}`, "kimi")),
];
const renderDashboard = (environments = cards) => render(
  <AccountPoolDashboard environments={environments} statsByCard={new Map()} statsLoading={false}
    renderCard={(environment) => <p>{environment.name}</p>} quotaRefreshStatus={null}
    onRefreshQuotas={vi.fn()} refreshingQuotas={false} />,
);
const supplier = (name: string) => within(screen.getByRole("region", { name }));

describe("supplier dashboard pagination and filters", () => {
  beforeEach(async () => {
    window.localStorage.clear();
    await i18n.changeLanguage("zh-CN");
  });

  it("limits each supplier to 4 cards and keeps their pages independent", () => {
    renderDashboard();
    expect(supplier("OpenAI Codex").getAllByRole("listitem")).toHaveLength(4);
    expect(supplier("Kimi").getAllByRole("listitem")).toHaveLength(4);
    expect(supplier("OpenAI Codex").getByRole("button", { name: "上一页" })).toBeDisabled();
    fireEvent.click(supplier("OpenAI Codex").getByRole("button", { name: "下一页" }));
    expect(supplier("OpenAI Codex").getByText("Codex 5")).toBeInTheDocument();
    expect(supplier("OpenAI Codex").queryByText("Codex 1")).not.toBeInTheDocument();
    expect(supplier("Kimi").getByText("Kimi 1")).toBeInTheDocument();
    for (let page = 2; page < 7; page += 1) {
      fireEvent.click(supplier("OpenAI Codex").getByRole("button", { name: "下一页" }));
    }
    expect(supplier("OpenAI Codex").getAllByRole("listitem")).toHaveLength(1);
    expect(supplier("OpenAI Codex").getByText("第 7 / 7 页")).toBeInTheDocument();
    expect(supplier("OpenAI Codex").getByRole("button", { name: "下一页" })).toBeDisabled();
  });

  it.each([8, 12])("changes page size to %i and returns all suppliers to their first page", async (pageSize) => {
    const user = userEvent.setup();
    renderDashboard();
    fireEvent.click(supplier("OpenAI Codex").getByRole("button", { name: "下一页" }));
    fireEvent.click(supplier("Kimi").getByRole("button", { name: "下一页" }));
    await user.click(screen.getByRole("combobox", { name: "每页卡片数" }));
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(["4", "8", "12"]);
    await user.click(screen.getByRole("option", { name: String(pageSize) }));
    expect(supplier("OpenAI Codex").getAllByRole("listitem")).toHaveLength(pageSize);
    expect(supplier("OpenAI Codex").getByText("Codex 1")).toBeInTheDocument();
    expect(supplier("Kimi").getAllByRole("listitem")).toHaveLength(pageSize);
    expect(supplier("Kimi").getByText("Kimi 1")).toBeInTheDocument();
  });

  it("searches before pagination, resets pages, and leaves global metrics unchanged", () => {
    renderDashboard();
    fireEvent.click(supplier("OpenAI Codex").getByRole("button", { name: "下一页" }));
    const search = screen.getByRole("textbox", { name: "搜索卡片名称或 ID" });
    fireEvent.change(search, { target: { value: "  codex 25  " } });
    expect(supplier("OpenAI Codex").getByText("Codex 25")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Kimi" })).not.toBeInTheDocument();
    expect(screen.getByText("38 / 38")).toBeInTheDocument();
    fireEvent.change(search, { target: { value: "" } });
    expect(supplier("OpenAI Codex").getByText("Codex 1")).toBeInTheDocument();
  });

  it("combines enabled and runtime status filters and can clear an empty result", async () => {
    const user = userEvent.setup();
    renderDashboard([
      card("enabled error", "openai_codex", { status: "error" }),
      card("disabled error", "openai_codex", { status: "error", enabled: false }),
      card("ready", "openai_codex"),
    ]);
    await user.click(screen.getByRole("combobox", { name: "启用状态" }));
    await user.click(screen.getByRole("option", { name: "仅已停用" }));
    await user.click(screen.getByRole("combobox", { name: "运行状态" }));
    await user.click(screen.getByRole("option", { name: "异常" }));
    expect(supplier("OpenAI Codex").getAllByRole("listitem")).toHaveLength(1);
    expect(supplier("OpenAI Codex").getByText("disabled error")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "搜索卡片名称或 ID" }), { target: { value: "missing" } });
    expect(screen.getByText("没有匹配的环境")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));
    expect(supplier("OpenAI Codex").getAllByRole("listitem")).toHaveLength(3);
  });

  it("paginates the stored order and keeps a nonempty page when refreshed data shrinks", () => {
    window.localStorage.setItem("account-pool.card-order.openai_codex", JSON.stringify(cards.slice(0, 25).map((item) => item.id).reverse()));
    const view = renderDashboard();
    expect(supplier("OpenAI Codex").getByText("Codex 25")).toBeInTheDocument();
    expect(supplier("OpenAI Codex").queryByText("Codex 1")).not.toBeInTheDocument();
    fireEvent.click(supplier("OpenAI Codex").getByRole("button", { name: "下一页" }));
    fireEvent.click(supplier("OpenAI Codex").getByRole("button", { name: "下一页" }));
    view.rerender(<AccountPoolDashboard environments={cards.slice(0, 3)} statsByCard={new Map()} statsLoading={false}
      renderCard={(environment) => <p>{environment.name}</p>} quotaRefreshStatus={null} onRefreshQuotas={vi.fn()} refreshingQuotas={false} />);
    expect(supplier("OpenAI Codex").getAllByRole("listitem")).toHaveLength(3);
    expect(supplier("OpenAI Codex").getByText("第 1 / 1 页")).toBeInTheDocument();
  });

  it("keeps the clamped supplier page when refreshed data grows again", () => {
    const view = renderDashboard(cards.slice(0, 9));
    fireEvent.click(supplier("OpenAI Codex").getByRole("button", { name: "下一页" }));
    fireEvent.click(supplier("OpenAI Codex").getByRole("button", { name: "下一页" }));
    expect(supplier("OpenAI Codex").getByText("第 3 / 3 页")).toBeInTheDocument();
    view.rerender(<AccountPoolDashboard environments={cards.slice(0, 8)} statsByCard={new Map()} statsLoading={false}
      renderCard={(environment) => <p>{environment.name}</p>} quotaRefreshStatus={null} onRefreshQuotas={vi.fn()} refreshingQuotas={false} />);
    expect(supplier("OpenAI Codex").getByText("第 2 / 2 页")).toBeInTheDocument();
    view.rerender(<AccountPoolDashboard environments={cards.slice(0, 9)} statsByCard={new Map()} statsLoading={false}
      renderCard={(environment) => <p>{environment.name}</p>} quotaRefreshStatus={null} onRefreshQuotas={vi.fn()} refreshingQuotas={false} />);
    expect(supplier("OpenAI Codex").getByText("第 2 / 3 页")).toBeInTheDocument();
    expect(supplier("OpenAI Codex").getByText("Codex 5")).toBeInTheDocument();
  });
});
