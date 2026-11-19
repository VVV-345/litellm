/** 构建逐账号订阅请求并顺序保存，失败项沿用原编号重试。 */
import { createCost, replaceCost, type CostCreate, type CostReport } from "../../api/AccountPoolCostsApi";
import { cycleEnd, parseCents } from "./costForm";

export type BatchCostRow = {
  account: CostReport["accounts"][number];
  selected: boolean;
  paidOn: string;
  startsOn: string;
  endsOn: string;
  automaticEnd: boolean;
};
export type BatchCostPlan = { name: string; amount: string; months: 1 | 3 | 12 };
export type BatchCostItem = { accountId: string; originalId?: string; body: CostCreate };
export type BatchCostResult = { accountId: string; error: string | null };

export function initialBatchRows(accounts: CostReport["accounts"], today: string): BatchCostRow[] {
  return accounts
    .filter((account) => !account.archived)
    .map((account) => ({
      account,
      selected: false,
      paidOn: account.latest?.paid_on ?? today,
      startsOn: account.latest?.starts_on ?? today,
      endsOn: account.latest?.ends_on ?? cycleEnd(today, 1),
      automaticEnd: !account.latest,
    }));
}

export function changeBatchCycle(rows: BatchCostRow[], months: BatchCostPlan["months"]): BatchCostRow[] {
  return rows.map((row) => (row.automaticEnd ? { ...row, endsOn: cycleEnd(row.startsOn, months) } : row));
}

export function buildBatchCosts(rows: BatchCostRow[], plan: BatchCostPlan, today: string): BatchCostItem[] {
  const selected = rows.filter((row) => row.selected && !row.account.archived);
  if (!selected.length) throw new Error("请至少选择一个账号");
  const cents = parseCents(plan.amount);
  if (!plan.name.trim() || plan.name.trim().length > 160 || cents === null)
    throw new Error("请填写套餐名称和每个账号的实付金额，金额最多两位小数");
  for (const row of selected) {
    const dates = [row.paidOn, row.startsOn, row.endsOn];
    const duration = (Date.parse(row.endsOn) - Date.parse(row.startsOn)) / 86_400_000;
    const invalidPeriod = duration < 0 || duration > 3660;
    if (dates.some((value) => !cycleEnd(value, 1)) || row.paidOn > today || invalidPeriod)
      throw new Error(`${row.account.account_name}：请填写有效日期，付款日期不能晚于今天，服务期不能超过十年`);
  }
  return selected.map((row) => ({
    accountId: row.account.account_id,
    originalId: row.account.latest?.id,
    body: {
      id: crypto.randomUUID(),
      category: "subscription",
      account_id: row.account.account_id,
      name: plan.name.trim(),
      amount_cents: cents,
      currency: "CNY",
      paid_on: row.paidOn,
      starts_on: row.startsOn,
      ends_on: row.endsOn,
      cycle_months: plan.months,
      auto_renew: row.account.latest?.auto_renew === true,
      note: row.account.latest?.note ?? "",
    },
  }));
}

export async function saveCostBatch(
  accessToken: string,
  items: BatchCostItem[],
  onResult: (result: BatchCostResult) => void,
): Promise<void> {
  for (const item of items) {
    try {
      if (item.originalId) await replaceCost(accessToken, item.originalId, item.body);
      else await createCost(accessToken, item.body);
      onResult({ accountId: item.accountId, error: null });
    } catch (cause) {
      onResult({ accountId: item.accountId, error: cause instanceof Error ? cause.message : "保存失败，请重试" });
    }
  }
}
