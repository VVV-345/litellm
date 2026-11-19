/** 验证批量表单的逐账号保存、锁定和失败项重试交互。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { createCost, replaceCost, type CostReport } from "../../api/AccountPoolCostsApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import { BatchCostEntryDialog } from "./BatchCostEntryDialog";

vi.mock("../../api/AccountPoolCostsApi", () => ({ createCost: vi.fn(), replaceCost: vi.fn() }));
const accounts: CostReport["accounts"] = [
  { account_id: "one", account_name: "账号甲", archived: false, recorded: false, paid_cents: 0, period_cents: 0 },
  {
    account_id: "two",
    account_name: "账号乙",
    archived: false,
    recorded: true,
    paid_cents: 0,
    period_cents: 0,
    latest: {
      id: "original",
      category: "subscription",
      account_id: "two",
      name: "已购套餐",
      amount_cents: 11800,
      currency: "CNY",
      paid_on: "2026-10-01",
      starts_on: "2026-10-03",
      ends_on: "2026-11-05",
      auto_renew: true,
      cycle_months: 1,
      note: "原订单",
      created_at: "2026-10-01T00:00:00Z",
      created_by: "admin",
    },
  },
  { account_id: "three", account_name: "其他账号", archived: false, recorded: false, paid_cents: 0, period_cents: 0 },
  { account_id: "archived", account_name: "已归档", archived: true, recorded: false, paid_cents: 0, period_cents: 0 },
];
beforeEach(async () => {
  vi.resetAllMocks();
  await i18n.changeLanguage("zh-CN");
});
function openDialog() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const close = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <BatchCostEntryDialog accessToken="admin" accounts={accounts} onClose={close} />
    </QueryClientProvider>,
  );
  return { client, close };
}

it("applies one plan to selected accounts with independent dates and retries only a failed replacement", async () => {
  const { client, close } = openDialog();
  client.setQueryData(accountPoolQueryKeys.costs("admin", "2026-11-01", "2026-11-18"), {});
  expect(screen.queryByRole("checkbox", { name: "选择 已归档" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存 0 个账号费用" })).toBeDisabled();
  await userEvent.click(screen.getByRole("checkbox", { name: "选择 账号甲" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "选择 账号乙" }));
  expect(screen.getByLabelText("账号乙 的服务结束日期")).toHaveValue("2026-11-05");
  fireEvent.change(screen.getByLabelText("Coding Plan 预设（选填）"), { target: { value: "glm" } });
  fireEvent.change(screen.getByLabelText("套餐与计费周期"), { target: { value: "0" } });
  await userEvent.click(screen.getByRole("button", { name: "套用套餐" }));
  expect(screen.getByLabelText("每个账号实付（人民币 ¥）")).toHaveValue("118.00");
  expect(screen.getByLabelText("账号乙 的服务结束日期")).toHaveValue("2026-11-05");
  fireEvent.change(screen.getByLabelText("每个账号实付（人民币 ¥）"), { target: { value: "100" } });
  fireEvent.change(screen.getByLabelText("账号甲 的付款日期"), { target: { value: "2026-11-01" } });
  fireEvent.change(screen.getByLabelText("账号甲 的服务开始日期"), { target: { value: "2026-11-01" } });
  expect(screen.getByLabelText("账号甲 的服务结束日期")).toHaveValue("2026-11-30");
  expect(screen.getByRole("status")).toHaveTextContent("已选 2 个账号，每个 ¥100.00，合计 ¥200.00");
  vi.mocked(replaceCost).mockRejectedValueOnce(new Error("连接中断"));
  await userEvent.click(screen.getByRole("button", { name: "保存 2 个账号费用" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("连接中断");
  expect(await screen.findByRole("button", { name: "重试失败项" })).toBeEnabled();
  expect(screen.getByLabelText("每个账号实付（人民币 ¥）")).toBeDisabled();
  const selectedAccount = screen.getByRole("checkbox", { name: "选择 账号乙" });
  await userEvent.click(selectedAccount);
  expect(selectedAccount).toBeChecked();
  const expectedNew = {
    account_id: "one",
    amount_cents: 10000,
    starts_on: "2026-11-01",
    ends_on: "2026-11-30",
    auto_renew: false,
    name: "智谱 GLM Coding Plan Lite（月付）",
  };
  expect(createCost).toHaveBeenCalledWith("admin", expect.objectContaining(expectedNew));
  const expectedExisting = {
    account_id: "two",
    amount_cents: 10000,
    paid_on: "2026-10-01",
    starts_on: "2026-10-03",
    ends_on: "2026-11-05",
    auto_renew: true,
    note: "原订单",
  };
  expect(replaceCost).toHaveBeenCalledWith("admin", "original", expect.objectContaining(expectedExisting));
  await userEvent.click(screen.getByRole("button", { name: "重试失败项" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "重试失败项" })).not.toBeInTheDocument());
  expect(createCost).toHaveBeenCalledOnce();
  expect(vi.mocked(replaceCost).mock.calls[0]).toEqual(vi.mocked(replaceCost).mock.calls[1]);
  expect(client.getQueryState(accountPoolQueryKeys.costs("admin", "2026-11-01", "2026-11-18"))?.isInvalidated).toBe(
    true,
  );
  expect(close).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "完成" }));
  expect(close).toHaveBeenCalledOnce();
});

it("selects matching original plans and recalculates their own service end dates only on request", async () => {
  openDialog();
  fireEvent.change(screen.getByLabelText("搜索批量账号或原套餐"), { target: { value: "已购套餐" } });
  await userEvent.click(screen.getByRole("checkbox", { name: "全选筛选结果" }));
  expect(screen.getByLabelText("账号乙 的服务结束日期")).toHaveValue("2026-11-05");
  await userEvent.click(screen.getByRole("button", { name: "年付", pressed: false }));
  expect(screen.getByLabelText("账号乙 的服务结束日期")).toHaveValue("2026-11-05");
  await userEvent.click(screen.getByRole("button", { name: "按周期重算所选结束日期" }));
  expect(screen.getByLabelText("账号乙 的服务结束日期")).toHaveValue("2027-10-02");
  fireEvent.change(screen.getByLabelText("账号乙 的服务结束日期"), { target: { value: "2027-10-10" } });
  await userEvent.click(screen.getByRole("button", { name: "月付", pressed: false }));
  expect(screen.getByLabelText("账号乙 的服务结束日期")).toHaveValue("2027-10-10");
  await userEvent.click(screen.getByRole("button", { name: "保存 1 个账号费用" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("套餐名称");
  expect(createCost).not.toHaveBeenCalled();
  expect(replaceCost).not.toHaveBeenCalled();
});

it("locks account selection and dismissal until the pending save finishes", async () => {
  const pending = Promise.withResolvers<void>();
  vi.mocked(createCost).mockImplementationOnce(async (_, body) => {
    await pending.promise;
    return { ...body, created_at: "2026-11-19T00:00:00Z", created_by: "admin" };
  });
  const { close } = openDialog();
  const account = screen.getByRole("checkbox", { name: "选择 账号甲" });
  await userEvent.click(account);
  fireEvent.change(screen.getByLabelText("统一套餐名称"), { target: { value: "月费" } });
  fireEvent.change(screen.getByLabelText("每个账号实付（人民币 ¥）"), { target: { value: "99" } });
  await userEvent.click(screen.getByRole("button", { name: "保存 1 个账号费用" }));
  expect(screen.getByRole("button", { name: "保存中…" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "完成" })).toBeDisabled();
  expect(account).toHaveAttribute("aria-disabled", "true");
  await userEvent.click(account);
  expect(account).toBeChecked();
  await userEvent.keyboard("{Escape}");
  expect(close).not.toHaveBeenCalled();
  pending.resolve();
  expect(await screen.findByText("已保存 1 / 1 个账号")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "完成" })).toBeEnabled());
  await userEvent.click(screen.getByRole("button", { name: "完成" }));
  expect(close).toHaveBeenCalledOnce();
});
