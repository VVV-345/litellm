/** 验证成本口径切换、真实账号入口及作废后的报表刷新。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { getCosts, voidCost, type CostReport } from "../../api/AccountPoolCostsApi";
import { AccountPoolCostsPanel } from "./AccountPoolCostsPanel";

vi.mock("../../api/AccountPoolCostsApi", () => ({ getCosts: vi.fn(), voidCost: vi.fn(), createCost: vi.fn() }));
it("switches totals, keeps missing costs visible, and refreshes after an explicit void", async () => {
  await i18n.changeLanguage("zh-CN");
  const record = {
    id: "record",
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
  expect(screen.getAllByText("未录入")).toHaveLength(4);
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
