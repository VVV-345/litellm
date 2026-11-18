/** 成本管理展示人民币付款与期间成本，资源费用独立汇总，记录可追溯作废。 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "@/lib/toast";
import { getCosts, voidCost, type CostRecord, type CostReport } from "../../api/AccountPoolCostsApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import { CostEntryDialog, type CostEntryTarget } from "./CostEntryDialog";
import { chinaToday, costLabels, expiryLabel, money } from "./costForm";

type CostAccount = CostReport["accounts"][number];
function accountExpiry(account: CostAccount): string {
  if (account.archived) return "已归档";
  if (account.recorded && account.days_until_expiry == null) return "服务尚未开始";
  return expiryLabel(account.days_until_expiry);
}
function AccountCostRow({
  a,
  mode,
  setTarget,
}: {
  a: CostAccount;
  mode: "period" | "paid";
  setTarget: (target: CostEntryTarget) => void;
}) {
  return (
    <TableRow key={a.account_id}>
      <TableCell className="font-medium">
        {a.account_name}
        {a.archived && (
          <Badge variant="outline" className="ml-2">
            历史账号
          </Badge>
        )}
      </TableCell>
      <TableCell>
        {a.latest ? (
          <>
            <p>{a.latest.name}</p>
            <p className="text-xs text-muted-foreground">
              {a.latest.starts_on} 至 {a.latest.ends_on}
            </p>
          </>
        ) : (
          "未录入"
        )}
      </TableCell>
      <TableCell className="text-right tabular-nums">{a.latest ? money(a.latest.amount_cents) : "未录入"}</TableCell>
      <TableCell className="text-right tabular-nums">
        {a.recorded ? money(mode === "period" ? a.period_cents : a.paid_cents) : "未录入"}
      </TableCell>
      <TableCell>{accountExpiry(a)}</TableCell>
      <TableCell>
        {!a.archived && (
          <div className="flex gap-1">
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                setTarget({
                  category: "subscription",
                  account: { id: a.account_id, name: a.account_name },
                })
              }
            >
              录入
            </Button>
            {a.latest && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() =>
                  setTarget({
                    category: "subscription",
                    account: { id: a.account_id, name: a.account_name },
                    renewal: a.latest!,
                  })
                }
              >
                续费
              </Button>
            )}
          </div>
        )}
      </TableCell>
    </TableRow>
  );
}

export function AccountPoolCostsPanel({ accessToken, active = true }: { accessToken: string; active?: boolean }) {
  const today = chinaToday();
  const [range, setRange] = useState({ start: `${today.slice(0, 7)}-01`, end: today });
  const [mode, setMode] = useState<"period" | "paid">("period");
  const [target, setTarget] = useState<CostEntryTarget | null>(null);
  const [search, setSearch] = useState("");
  const [recordPage, setRecordPage] = useState(0);
  const [voiding, setVoiding] = useState<CostRecord | null>(null);
  const valid = Boolean(range.start && range.end && range.start <= range.end);
  const queryOptions = {
    queryKey: accountPoolQueryKeys.costs(accessToken, range.start, range.end),
    queryFn: () => getCosts(accessToken, range.start, range.end),
    enabled: active && valid,
    retry: false,
    refetchOnMount: "always" as const,
  };
  const query = useQuery(queryOptions);
  const data = query.data;
  const accounts =
    data?.accounts.filter((account) => account.account_name.toLowerCase().includes(search.toLowerCase())) ?? [];
  const records = data?.records ?? [];
  const page = Math.min(recordPage, Math.max(0, Math.ceil(records.length / 20) - 1));
  const setDates = (next: typeof range) => {
    setRange(next);
    setRecordPage(0);
  };
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">成本管理</h2>
          <p className="mt-1 text-sm text-muted-foreground">人民币台账 · 订阅关联已有账号，代理与服务器独立记账</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setTarget({ category: "proxy" })}>
            录入代理费用
          </Button>
          <Button variant="outline" onClick={() => setTarget({ category: "server" })}>
            录入服务器费用
          </Button>
        </div>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="cost-range-start">开始日期</Label>
          <Input
            id="cost-range-start"
            type="date"
            value={range.start}
            onChange={(e) => setDates({ ...range, start: e.target.value })}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="cost-range-end">结束日期</Label>
          <Input
            id="cost-range-end"
            type="date"
            min={range.start}
            value={range.end}
            onChange={(e) => setDates({ ...range, end: e.target.value })}
          />
        </div>
        <Button variant="ghost" onClick={() => setDates({ start: `${today.slice(0, 7)}-01`, end: today })}>
          本月至今
        </Button>
        <div className="flex gap-1" role="group" aria-label="成本统计口径">
          <Button
            variant={mode === "period" ? "default" : "outline"}
            onClick={() => setMode("period")}
            aria-pressed={mode === "period"}
          >
            期间成本
          </Button>
          <Button
            variant={mode === "paid" ? "default" : "outline"}
            onClick={() => setMode("paid")}
            aria-pressed={mode === "paid"}
          >
            实际付款
          </Button>
        </div>
        <Button variant="ghost" disabled={!valid || query.isFetching} onClick={() => void query.refetch()}>
          刷新
        </Button>
      </div>
      {!valid && (
        <p role="alert" className="text-destructive">
          请选择有效的起止日期
        </p>
      )}
      {query.isError && (
        <p role="alert" className="text-destructive">
          成本读取失败：{query.error.message}，请点击刷新重试
        </p>
      )}
      {valid && query.isPending && <p role="status">正在读取成本…</p>}
      {valid && data && (
        <>
          <CostSummary data={data} mode={mode} />
          <AccountCosts accounts={accounts} mode={mode} setTarget={setTarget} search={search} setSearch={setSearch} />
          <CostRecords data={data} records={records} page={page} setRecordPage={setRecordPage} beginVoid={setVoiding} />
        </>
      )}
      {target && <CostEntryDialog accessToken={accessToken} target={target} onClose={() => setTarget(null)} />}
      {voiding && <VoidCostDialog record={voiding} accessToken={accessToken} onClose={() => setVoiding(null)} />}
    </div>
  );
}

function CostRecords({
  data,
  records,
  page,
  setRecordPage,
  beginVoid,
}: {
  data: CostReport;
  records: CostRecord[];
  page: number;
  setRecordPage: (page: number) => void;
  beginVoid: (record: CostRecord) => void;
}) {
  return (
    <section className="space-y-3">
      <h3 className="font-semibold">
        费用记录{" "}
        <span className="text-sm font-normal text-muted-foreground">
          {data.record_count} 条 · 付款日或服务期与所选期间相交
        </span>
      </h3>
      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>类型 / 归属</TableHead>
              <TableHead>名称</TableHead>
              <TableHead className="text-right">付款金额</TableHead>
              <TableHead>付款日 / 服务期</TableHead>
              <TableHead>备注 / 状态</TableHead>
              <TableHead>操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {records.slice(page * 20, page * 20 + 20).map((r) => (
              <TableRow key={r.id} className={r.voided_at ? "text-muted-foreground" : ""}>
                <TableCell>
                  {costLabels[r.category]}
                  <p className="text-xs">{r.account_name ?? "独立费用"}</p>
                </TableCell>
                <TableCell>{r.name}</TableCell>
                <TableCell className="text-right tabular-nums">{money(r.amount_cents)}</TableCell>
                <TableCell>
                  {r.paid_on}
                  <p className="text-xs text-muted-foreground">
                    {r.starts_on} 至 {r.ends_on}
                  </p>
                </TableCell>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  {r.note || "无备注"}
                  {r.voided_at && <p>已作废：{r.void_reason}</p>}
                </TableCell>
                <TableCell>
                  {!r.voided_at && (
                    <Button size="sm" variant="ghost" onClick={() => beginVoid(r)}>
                      作废
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
            {!records.length && (
              <TableRow>
                <TableCell colSpan={6} className="py-8 text-center text-muted-foreground">
                  该期间没有费用记录
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      <div className="flex items-center justify-end gap-3">
        <Button size="sm" variant="outline" disabled={page === 0} onClick={() => setRecordPage(page - 1)}>
          上一页
        </Button>
        <span className="text-sm">
          {page + 1} / {Math.max(1, Math.ceil(records.length / 20))}
        </span>
        <Button
          size="sm"
          variant="outline"
          disabled={(page + 1) * 20 >= records.length}
          onClick={() => setRecordPage(page + 1)}
        >
          下一页
        </Button>
      </div>
    </section>
  );
}

function CostSummary({ data, mode }: { data: CostReport; mode: "period" | "paid" }) {
  const totals = data[mode];
  return (
    <>
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border bg-border lg:grid-cols-4">
        {(
          [
            ["合计", totals.total_cents],
            ["账号订阅", totals.subscription_cents],
            ["代理", totals.proxy_cents],
            ["服务器", totals.server_cents],
          ] as const
        ).map(([title, amount]) => (
          <div key={title} className="bg-background p-4">
            <p className="text-sm text-muted-foreground">{title}</p>
            <p className="mt-2 text-2xl font-semibold tabular-nums">{money(amount)}</p>
          </div>
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        {mode === "period"
          ? "按已录入费用的服务天数计算所选期间成本，未来日期包含预付服务成本，不代表已经使用"
          : "按付款日期汇总已确认支出，不包含作废记录"}
        。不包含模型估算费用；未录入的费用不计入合计
      </p>
      <div className="flex flex-wrap gap-2" role="status">
        <Badge variant="outline">未来 7 天到期：{data.expiring_count} 个账号</Badge>
        <Badge variant="outline">已到期未录入续费：{data.overdue_count} 个账号</Badge>
        <span className="text-xs text-muted-foreground">
          按北京时间 {data.today} 检查，仅提示已记录订阅，不自动付款或停卡
        </span>
      </div>
    </>
  );
}

function VoidCostDialog({
  record,
  accessToken,
  onClose,
}: {
  record: CostRecord;
  accessToken: string;
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const cache = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => voidCost(accessToken, record.id, reason.trim()),
    onSuccess: async () => {
      await cache.invalidateQueries({ queryKey: accountPoolQueryKeys.costsRoot(accessToken) });
      onClose();
      toast.success("费用已作废，原记录保留");
    },
  });
  return (
    <Dialog
      open={true}
      onOpenChange={(open) => {
        if (!open && !mutation.isPending) onClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>作废费用记录</DialogTitle>
          <DialogDescription>
            作废“{record.name}”后不再计入成本，原记录和原因保留。金额或日期误录可作废后重新录入，此操作不表示退款
          </DialogDescription>
        </DialogHeader>
        <Label htmlFor="cost-void-reason">作废原因</Label>
        <Input
          id="cost-void-reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          maxLength={500}
          disabled={mutation.isPending}
        />
        {mutation.isError && (
          <p role="alert" className="text-destructive">
            {mutation.error.message}
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onClose()} disabled={mutation.isPending}>
            取消
          </Button>
          <Button
            variant="destructive"
            onClick={() => mutation.mutate()}
            disabled={!reason.trim() || mutation.isPending}
          >
            {mutation.isPending ? "处理中…" : "确认作废"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AccountCosts({
  accounts,
  mode,
  setTarget,
  search,
  setSearch,
}: {
  accounts: CostAccount[];
  mode: "period" | "paid";
  setTarget: (target: CostEntryTarget) => void;
  search: string;
  setSearch: (value: string) => void;
}) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="font-semibold">账号订阅</h3>
        <Input
          className="max-w-xs"
          aria-label="筛选账号"
          placeholder="搜索账号名称"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>账号</TableHead>
              <TableHead>最近服务期 / 套餐</TableHead>
              <TableHead className="text-right">最近一笔订阅金额</TableHead>
              <TableHead className="text-right">{mode === "period" ? "期间成本" : "实际付款"}</TableHead>
              <TableHead>到期提醒</TableHead>
              <TableHead>操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {accounts.map((a) => (
              <AccountCostRow key={a.account_id} a={a} mode={mode} setTarget={setTarget} />
            ))}
            {!accounts.length && (
              <TableRow>
                <TableCell colSpan={6} className="py-8 text-center text-muted-foreground">
                  {search ? "没有匹配的账号" : "暂无账号，请先在号池添加真实账号，再录入订阅费用"}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}
