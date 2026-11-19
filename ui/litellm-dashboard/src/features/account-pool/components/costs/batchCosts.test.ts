/** 验证批量费用的日期隔离、金额校验和逐项幂等重试。 */
import { beforeEach, expect, it, vi } from "vitest";
import { createCost, replaceCost, type CostReport } from "../../api/AccountPoolCostsApi";
import { buildBatchCosts, changeBatchCycle, initialBatchRows, saveCostBatch } from "./batchCosts";

vi.mock("../../api/AccountPoolCostsApi", () => ({ createCost: vi.fn(), replaceCost: vi.fn() }));
const today = "2026-11-18";
const accounts: CostReport["accounts"] = [
  {
    account_id: "existing",
    account_name: "已录入账号",
    archived: false,
    recorded: true,
    paid_cents: 0,
    period_cents: 0,
    latest: {
      id: "original",
      category: "subscription",
      account_id: "existing",
      name: "原套餐",
      amount_cents: 9900,
      currency: "CNY",
      paid_on: "2026-10-01",
      starts_on: "2026-10-03",
      ends_on: "2026-11-05",
      note: "原订单",
      cycle_months: 1,
      auto_renew: true,
      created_at: "2026-10-01T00:00:00Z",
      created_by: "admin",
    },
  },
  { account_id: "new", account_name: "未录入账号", archived: false, recorded: false, paid_cents: 0, period_cents: 0 },
  { account_id: "archived", account_name: "历史账号", archived: true, recorded: true, paid_cents: 0, period_cents: 0 },
];
const plan = { name: "统一月费", amount: "0.29", months: 1 as const };
beforeEach(() => vi.resetAllMocks());

it("preserves independent recorded dates and recalculates only automatic dates on cycle changes", () => {
  const rows = initialBatchRows(accounts, today);
  expect(rows.map((row) => row.account.account_id)).toEqual(["existing", "new"]);
  expect(rows.every((row) => !row.selected)).toBe(true);
  const changed = changeBatchCycle(rows, 12);
  const originalDates = {
    paidOn: "2026-10-01",
    startsOn: "2026-10-03",
    endsOn: "2026-11-05",
    automaticEnd: false,
  };
  expect(changed[0]).toMatchObject(originalDates);
  expect(changed[1]).toMatchObject({ paidOn: today, startsOn: today, endsOn: "2027-11-17" });
});

it("builds one price for selected real accounts while preserving each period, note and renewal setting", () => {
  const rows = initialBatchRows(accounts, today).map((row) => ({ ...row, selected: true }));
  const batch = buildBatchCosts(rows, plan, today);
  expect(batch).toHaveLength(2);
  expect(batch[0]).toMatchObject({
    originalId: "original",
    body: {
      account_id: "existing",
      amount_cents: 29,
      name: "统一月费",
      paid_on: "2026-10-01",
      starts_on: "2026-10-03",
      ends_on: "2026-11-05",
      note: "原订单",
      auto_renew: true,
    },
  });
  expect(batch[1]).toMatchObject({
    body: { account_id: "new", amount_cents: 29, starts_on: today, ends_on: "2026-12-17", auto_renew: false, note: "" },
  });
  expect(batch[0].body.id).not.toBe(batch[1].body.id);
  const unselectedExisting = { ...rows[0], selected: false };
  expect(buildBatchCosts([unselectedExisting, rows[1]], plan, today)).toHaveLength(1);
});

it.each([
  { paidOn: "2026-11-19" },
  { startsOn: "" },
  { endsOn: "2026-02-30" },
  { endsOn: "2026-01-01" },
  { endsOn: "2037-01-01" },
])("rejects invalid selected dates before any writes: %j", (invalid) => {
  const rows = initialBatchRows(accounts, today).map((row) => ({ ...row, selected: true }));
  expect(() => buildBatchCosts([{ ...rows[1], ...invalid }], plan, today)).toThrow("未录入账号");
  expect(createCost).not.toHaveBeenCalled();
  expect(replaceCost).not.toHaveBeenCalled();
});

it("validates selection and exact price but ignores invalid dates of unselected accounts", () => {
  const rows = initialBatchRows(accounts, today);
  expect(() => buildBatchCosts(rows, plan, today)).toThrow("至少选择");
  const selected = [
    { ...rows[0], selected: true },
    { ...rows[1], startsOn: "" },
  ];
  expect(() => buildBatchCosts(selected, { ...plan, amount: "1.001" }, today)).toThrow("实付金额");
  const unnamed = { ...plan, name: " " };
  expect(() => buildBatchCosts(selected, unnamed, today)).toThrow("套餐名称");
  expect(buildBatchCosts(selected, plan, today)).toHaveLength(1);
});

it("continues after a failed account and replays the same replacement request on retry", async () => {
  const batch = buildBatchCosts(
    initialBatchRows(accounts, today).map((row) => ({ ...row, selected: true })),
    plan,
    today,
  );
  vi.mocked(replaceCost).mockRejectedValueOnce(new Error("连接中断"));
  const receive = vi.fn();
  await saveCostBatch("admin", batch, receive);
  expect(receive.mock.calls).toEqual([
    [{ accountId: "existing", error: "连接中断" }],
    [{ accountId: "new", error: null }],
  ]);
  expect(createCost).toHaveBeenCalledOnce();
  await saveCostBatch("admin", [batch[0]], receive);
  expect(vi.mocked(replaceCost).mock.calls[0]).toEqual(vi.mocked(replaceCost).mock.calls[1]);
  expect(createCost).toHaveBeenCalledOnce();
  expect(receive).toHaveBeenLastCalledWith({ accountId: "existing", error: null });
});
