/** 批量录入账号订阅，保留独立日期及失败项重试编号。 */
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { CostReport } from "../../api/AccountPoolCostsApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import { CodingPlanPresetPicker } from "./CodingPlanPresetPicker";
import { chinaToday, cycleEnd, money, parseCents } from "./costForm";
import {
  buildBatchCosts,
  changeBatchCycle,
  initialBatchRows,
  saveCostBatch,
  type BatchCostItem,
  type BatchCostPlan,
  type BatchCostResult,
  type BatchCostRow,
} from "./batchCosts";

export function BatchCostEntryAction({ accessToken, report }: { accessToken: string; report?: CostReport }) {
  const [open, setOpen] = useState(false);
  const accounts = report?.accounts ?? [];
  return (
    <>
      <Button disabled={!accounts.some((account) => !account.archived)} onClick={() => setOpen(true)}>
        批量录入账号费用
      </Button>
      {open && <BatchCostEntryDialog accessToken={accessToken} accounts={accounts} onClose={() => setOpen(false)} />}
    </>
  );
}

export function BatchCostEntryDialog({
  accessToken,
  accounts,
  onClose,
}: {
  accessToken: string;
  accounts: CostReport["accounts"];
  onClose: () => void;
}) {
  const today = chinaToday();
  const cache = useQueryClient();
  const [rows, setRows] = useState(() => initialBatchRows(accounts, today));
  const [plan, setPlan] = useState<BatchCostPlan>({ name: "", amount: "", months: 1 });
  const [search, setSearch] = useState("");
  const [batch, setBatch] = useState<BatchCostItem[] | null>(null);
  const [results, setResults] = useState<Record<string, BatchCostResult>>({});
  const [error, setError] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: (items: BatchCostItem[]) =>
      saveCostBatch(accessToken, items, (result) =>
        setResults((current) => ({ ...current, [result.accountId]: result })),
      ),
    onSuccess: () => cache.invalidateQueries({ queryKey: accountPoolQueryKeys.costsRoot(accessToken) }),
  });
  const locked = batch !== null;
  const selected = rows.filter((row) => row.selected);
  const visible = locked
    ? selected
    : rows.filter((row) =>
        `${row.account.account_name} ${row.account.latest?.name ?? ""}`.toLowerCase().includes(search.toLowerCase()),
      );
  const saved = Object.values(results).filter((result) => result.error === null).length;
  const remaining = batch?.filter((item) => results[item.accountId]?.error !== null) ?? [];
  const cents = parseCents(plan.amount);
  const updateRow = (id: string, values: Partial<BatchCostRow>) =>
    setRows((current) => current.map((row) => (row.account.account_id === id ? { ...row, ...values } : row)));
  const changeCycle = (months: BatchCostPlan["months"]) => {
    setPlan((current) => ({ ...current, months }));
    setRows((current) => changeBatchCycle(current, months));
  };
  const submit = () => {
    if (mutation.isPending) return;
    try {
      const items = batch ? remaining : buildBatchCosts(rows, plan, today);
      setBatch((current) => current ?? items);
      setError(null);
      mutation.mutate(items);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "请检查录入内容");
    }
  };
  return (
    <Dialog open onOpenChange={(open) => !open && !mutation.isPending && onClose()}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-3xl" showCloseButton={!mutation.isPending}>
        <DialogHeader>
          <DialogTitle>批量录入账号费用</DialogTitle>
          <DialogDescription>
            套餐、每个账号的实付金额和周期统一设置，日期分别填写。已有费用将替换最近一笔，旧记录保留且不重复计费
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <fieldset disabled={locked} className="space-y-4">
            <CodingPlanPresetPicker
              onApply={(name, amount, months) => {
                setPlan({ name, amount, months });
                setRows((current) => changeBatchCycle(current, months));
              }}
            />
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="batch-cost-name">统一套餐名称</Label>
                <Input
                  id="batch-cost-name"
                  value={plan.name}
                  maxLength={160}
                  onChange={(event) => setPlan({ ...plan, name: event.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="batch-cost-amount">每个账号实付（人民币 ¥）</Label>
                <Input
                  id="batch-cost-amount"
                  value={plan.amount}
                  inputMode="decimal"
                  placeholder="0.00"
                  onChange={(event) => setPlan({ ...plan, amount: event.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label id="batch-cost-cycle-label">统一计费周期</Label>
              <div role="group" aria-labelledby="batch-cost-cycle-label" className="flex gap-2">
                {(
                  [
                    { months: 1, label: "月付" },
                    { months: 3, label: "季付" },
                    { months: 12, label: "年付" },
                  ] as const
                ).map(({ months, label }) => (
                  <Button
                    key={months}
                    type="button"
                    className="flex-1"
                    variant={plan.months === months ? "default" : "outline"}
                    aria-pressed={plan.months === months}
                    onClick={() => changeCycle(months)}
                  >
                    {label}
                  </Button>
                ))}
              </div>
            </div>
            <Input
              aria-label="搜索批量账号或原套餐"
              placeholder="搜索账号或原套餐"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            <div className="flex flex-wrap items-center justify-between gap-3">
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={visible.length > 0 && visible.every((row) => row.selected)}
                  indeterminate={visible.some((row) => row.selected) && !visible.every((row) => row.selected)}
                  disabled={locked || !visible.length}
                  onCheckedChange={(checked) =>
                    setRows((current) =>
                      current.map((row) =>
                        visible.some((item) => item.account.account_id === row.account.account_id)
                          ? { ...row, selected: checked }
                          : row,
                      ),
                    )
                  }
                />
                全选筛选结果
              </label>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={!selected.length}
                onClick={() =>
                  setRows((current) =>
                    current.map((row) =>
                      row.selected ? { ...row, endsOn: cycleEnd(row.startsOn, plan.months), automaticEnd: true } : row,
                    ),
                  )
                }
              >
                按周期重算所选结束日期
              </Button>
            </div>
          </fieldset>
          <p className="text-xs text-muted-foreground">
            已有账号保留原日期、备注和自动续期设置；新账号默认不续期。修改开始日期会按统一周期计算结束日期，结束日期也可手改
          </p>
          <fieldset disabled={locked} className="space-y-3">
            {visible.map((row) => (
              <BatchAccountDates
                key={row.account.account_id}
                row={row}
                today={today}
                months={plan.months}
                locked={locked}
                result={results[row.account.account_id]}
                update={(values) => updateRow(row.account.account_id, values)}
              />
            ))}
            {!visible.length && <p className="py-4 text-center text-muted-foreground">没有匹配的可录入账号</p>}
          </fieldset>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <BatchCostFooter
            selected={selected.length}
            cents={cents}
            total={batch?.length ?? null}
            saved={saved}
            remaining={remaining.length}
            pending={mutation.isPending}
            onClose={onClose}
          />
        </form>
      </DialogContent>
    </Dialog>
  );
}

function BatchCostFooter({
  selected,
  cents,
  total,
  saved,
  remaining,
  pending,
  onClose,
}: {
  selected: number;
  cents: number | null;
  total: number | null;
  saved: number;
  remaining: number;
  pending: boolean;
  onClose: () => void;
}) {
  const locked = total !== null;
  const canRetry = locked && remaining > 0;
  const showRetryHint = canRetry && !pending;
  const retryHint = showRetryHint ? `，${remaining} 个未保存，可重试` : "";
  const amountHint = cents !== null ? `，每个 ${money(cents)}，合计 ${money(cents * selected)}` : "";
  const summary = locked ? `已保存 ${saved} / ${total} 个账号${retryHint}` : `已选 ${selected} 个账号${amountHint}`;
  const saveLabel = locked ? "重试失败项" : `保存 ${selected} 个账号费用`;
  return (
    <>
      <p role="status" className="text-sm font-medium">
        {summary}
      </p>
      {showRetryHint && (
        <p className="text-xs text-muted-foreground">
          重试保留本次内容，仅处理未保存账号。需要修改时，请关闭后重新录入
        </p>
      )}
      <DialogFooter>
        <Button type="button" variant="outline" disabled={pending} onClick={onClose}>
          {locked ? "完成" : "取消"}
        </Button>
        {(!locked || canRetry) && (
          <Button type="submit" disabled={pending || !selected}>
            {pending ? "保存中…" : saveLabel}
          </Button>
        )}
      </DialogFooter>
    </>
  );
}

function BatchAccountDates({
  row,
  today,
  months,
  locked,
  result,
  update,
}: {
  row: BatchCostRow;
  today: string;
  months: BatchCostPlan["months"];
  locked: boolean;
  result?: BatchCostResult;
  update: (values: Partial<BatchCostRow>) => void;
}) {
  const { account } = row;
  return (
    <section className="space-y-3 rounded-lg border p-3" aria-label={account.account_name}>
      <div className="flex items-center gap-2 font-medium">
        <Checkbox
          id={`batch-select-${account.account_id}`}
          aria-labelledby={`batch-label-${account.account_id}`}
          checked={row.selected}
          disabled={locked}
          onCheckedChange={(selected) => update({ selected })}
        />
        <Label htmlFor={`batch-select-${account.account_id}`}>
          <span id={`batch-label-${account.account_id}`} className="sr-only">{`选择 ${account.account_name}`}</span>
          {account.account_name}
        </Label>
        <span className="ml-auto text-xs font-normal text-muted-foreground">
          {account.latest ? "替换最近一笔" : "新增费用"}
        </span>
      </div>
      {account.latest && (
        <p className="text-xs text-muted-foreground">
          原套餐：{account.latest.name} · {money(account.latest.amount_cents)} ·{" "}
          {account.latest.auto_renew ? "自动续期已开启" : "到期不自动续期"}
        </p>
      )}
      {row.selected && (
        <div className="grid gap-3 sm:grid-cols-3">
          {(
            [
              { key: "paidOn", label: "付款日期" },
              { key: "startsOn", label: "服务开始日期" },
              { key: "endsOn", label: "服务结束日期" },
            ] as const
          ).map(({ key, label }) => (
            <div key={key} className="space-y-1">
              <Label htmlFor={`batch-${key}-${account.account_id}`}>{label}</Label>
              <Input
                id={`batch-${key}-${account.account_id}`}
                aria-label={`${account.account_name} 的${label}`}
                type="date"
                value={row[key]}
                max={key === "paidOn" ? today : undefined}
                min={key === "endsOn" ? row.startsOn : undefined}
                onChange={(event) => {
                  const value = event.target.value;
                  if (key === "startsOn")
                    update({ startsOn: value, endsOn: cycleEnd(value, months), automaticEnd: true });
                  else if (key === "endsOn") update({ endsOn: value, automaticEnd: false });
                  else update({ paidOn: value });
                }}
              />
            </div>
          ))}
        </div>
      )}
      {result && (
        <p
          role={result.error === null ? "status" : "alert"}
          className={result.error === null ? "text-sm" : "text-sm text-destructive"}
        >
          {result.error === null ? "已保存" : `保存失败：${result.error}`}
        </p>
      )}
    </section>
  );
}
