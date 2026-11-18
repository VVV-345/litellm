/** 验证成本口径切换、真实账号入口及作废后的报表刷新。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { getCosts, stopCostRenewal, voidCost, type CostReport } from "../../api/AccountPoolCostsApi";
import { AccountPoolCostsPanel } from "./AccountPoolCostsPanel";

vi.mock("../../api/AccountPoolCostsApi", () => ({
  getCosts: vi.fn(),
  voidCost: vi.fn(),
  stopCostRenewal: vi.fn(),
  createCost: vi.fn(),
  replaceCost: vi.fn(),
}));
it("switches totals, keeps missing costs visible, and refreshes after an explicit void", async () => {
  await i18n.changeLanguage("zh-CN");
  const record = {
    id: "record",
    auto_renew: false,
    category: "server" as const,
    name: "服务器月费",
    amount_cents: 3100,
    currency: "CNY" as const,
    note: "",
    paid_on: "2026-01-01",
    starts_on: "2026-01-01",
    ends_on: "2026-01-31",
    created_at: "2026-01-01T00:00:00Z",
    created_by: "admin",
  };
  const report: CostReport = {
    currency: "CNY",
    starts_on: "2026-01-01",
    ends_on: "2026-01-10",
    today: "2026-01-10",
    paid: { subscription_cents: 0, proxy_cents: 0, server_cents: 3100, total_cents: 3100 },
    period: { subscription_cents: 0, proxy_cents: 0, server_cents: 1000, total_cents: 1000 },
    accounts: [
      {
        account_id: "existing",
        account_name: "真实账号",
        archived: false,
        recorded: false,
        paid_cents: 0,
        period_cents: 0,
      },
    ],
    records: [record],
    record_count: 1,
    expiring_count: 0,
    overdue_count: 0,
  };
  vi.mocked(getCosts).mockResolvedValue(report);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  render(
    <QueryClientProvider client={client}>
      <AccountPoolCostsPanel accessToken="admin" />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("真实账号")).toBeInTheDocument();
  expect(screen.getAllByText("¥10.00")).toHaveLength(2);
  expect(screen.getAllByText("未录入")).toHaveLength(3);
  await userEvent.click(screen.getByRole("button", { name: "实际付款" }));
  expect(screen.queryByText("¥10.00")).not.toBeInTheDocument();
  expect(screen.getAllByText("¥31.00")).toHaveLength(3);
  await userEvent.click(screen.getByRole("button", { name: "录入" }));
  expect(await screen.findByText(/关联账号：真实账号/)).toBeInTheDocument();
  await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "取消" }));
  await userEvent.click(screen.getByRole("button", { name: "作废" }));
  expect(screen.getByRole("button", { name: "确认作废" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("作废原因"), { target: { value: "重复登记" } });
  expect(voidCost).not.toHaveBeenCalled();
  vi.mocked(voidCost).mockResolvedValueOnce({ ...record, voided_at: "2026-01-10T00:00:00Z", void_reason: "重复登记" });
  await userEvent.click(screen.getByRole("button", { name: "确认作废" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(voidCost).toHaveBeenCalledWith("admin", "record", "重复登记");
  expect(getCosts).toHaveBeenCalledTimes(2);
});

it("displays automatic renewal and stops it through the account action", async () => {
  const latest = {
    id: "subscription",
    note: "",
    category: "subscription" as const,
    account_id: "existing",
    name: "月费",
    amount_cents: 11800,
    currency: "CNY" as const,
    paid_on: "2026-11-01",
    starts_on: "2026-11-01",
    ends_on: "2026-11-30",
    auto_renew: true,
    cycle_months: 1 as const,
    created_at: "2026-11-01T00:00:00Z",
    created_by: "admin",
  };
  const totals = { subscription_cents: 11800, proxy_cents: 0, server_cents: 0, total_cents: 11800 };
  const report: CostReport = {
    currency: "CNY",
    starts_on: "2026-11-01",
    ends_on: "2026-11-18",
    today: "2026-11-18",
    paid: totals,
    period: totals,
    records: [latest],
    record_count: 1,
    expiring_count: 0,
    overdue_count: 0,
    accounts: [
      {
        account_id: "existing",
        account_name: "续期账号",
        archived: false,
        recorded: true,
        paid_cents: 11800,
        period_cents: 11800,
        latest,
      },
    ],
  };
  vi.mocked(getCosts).mockResolvedValue(report);
  vi.mocked(stopCostRenewal).mockResolvedValue({ ...latest, auto_renew: false });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}>
      <AccountPoolCostsPanel accessToken="admin" />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("自动续期已开启")).toBeInTheDocument();
  expect(screen.queryByRole("columnheader", { name: "期间成本" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "录入" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "重新录入" }));
  expect(screen.getByLabelText("付款金额（人民币 ¥）")).toHaveValue("118.00");
  expect(screen.getByLabelText("付款日期")).toHaveValue("2026-11-01");
  expect(screen.getByLabelText("服务开始日期")).toHaveValue("2026-11-01");
  expect(screen.getByLabelText("服务结束日期")).toHaveValue("2026-11-30");
  expect(screen.getByRole("switch", { name: "到期后自动续期" })).toBeChecked();
  await userEvent.click(screen.getByRole("button", { name: "取消" }));
  await userEvent.click(screen.getByRole("button", { name: "关闭续期" }));
  await waitFor(() => expect(stopCostRenewal).toHaveBeenCalledWith("admin", "subscription"));
});
