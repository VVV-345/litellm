/** 真实账号订阅及独立资源费用的单条录入，保存失败保留幂等编号。 */
import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "@/lib/toast";
import {
  createCost,
  replaceCost,
  type CostCategory,
  type CostCreate,
  type CostRecord,
} from "../../api/AccountPoolCostsApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import { chinaToday, costLabels, cycleEnd, nextDate, parseCents } from "./costForm";
import { CodingPlanPresetPicker } from "./CodingPlanPresetPicker";

export type CostEntryTarget = {
  category: CostCategory;
  account?: { id: string; name: string };
  renewal?: CostRecord;
  editing?: CostRecord;
};
function initialValues(target: CostEntryTarget, today: string) {
  const previous = target.editing ?? target.renewal;
  const startsOn = target.editing?.starts_on ?? (target.renewal ? nextDate(target.renewal.ends_on) : today);
  const cycleMonths = previous?.cycle_months ?? null;
  const calculatedEnd = cycleMonths ? cycleEnd(startsOn, cycleMonths) : "";
  const endsOn = target.editing?.ends_on ?? calculatedEnd;
  return {
    name: previous?.name ?? (target.account ? `${target.account.name} 订阅` : ""),
    amount: previous ? (previous.amount_cents / 100).toFixed(2) : "",
    paidOn: target.editing?.paid_on ?? today,
    startsOn,
    cycleMonths,
    autoRenew: target.editing?.auto_renew === true,
    endsOn,
    automaticEnd: !target.editing || calculatedEnd === endsOn,
    note: target.editing?.note ?? "",
  };
}

export function CostEntryDialog({
  accessToken,
  target,
  onClose,
}: {
  accessToken: string;
  target: CostEntryTarget;
  onClose: () => void;
}) {
  const cache = useQueryClient();
  const today = chinaToday();
  const [initial] = useState(() => initialValues(target, today));
  const [name, setName] = useState(initial.name);
  const [amount, setAmount] = useState(initial.amount);
  const [paidOn, setPaidOn] = useState(initial.paidOn);
  const [startsOn, setStartsOn] = useState(initial.startsOn);
  const [cycleMonths, setCycleMonths] = useState(initial.cycleMonths);
  const [autoRenew, setAutoRenew] = useState(initial.autoRenew);
  const [endsOn, setEndsOn] = useState(initial.endsOn);
  const [automaticEnd, setAutomaticEnd] = useState(initial.automaticEnd);
  const [note, setNote] = useState(initial.note);
  const entryTitle = target.renewal ? "记录续费" : "录入费用";
  const title = target.editing ? "重新录入费用" : entryTitle;
  const [error, setError] = useState<string | null>(null);
  const attempt = useRef<{ payload: string; id: string } | null>(null);
  const mutation = useMutation({
    mutationFn: (body: CostCreate) =>
      target.editing ? replaceCost(accessToken, target.editing.id, body) : createCost(accessToken, body),
    onSuccess: async () => {
      await cache.invalidateQueries({ queryKey: accountPoolQueryKeys.costsRoot(accessToken) });
      toast.success(target.editing ? "费用已更新" : "费用已录入");
      onClose();
    },
    onError: (cause: Error) => setError(cause.message || "保存失败，请重试"),
  });
  const applyPreset = (presetName: string, presetAmount: string, months: 1 | 3 | 12) => {
    setName(presetName);
    setAmount(presetAmount);
    setCycleMonths(months);
    setEndsOn(cycleEnd(startsOn, months));
    setAutomaticEnd(true);
  };
  const changeStart = (value: string) => {
    setStartsOn(value);
    if (automaticEnd && cycleMonths) setEndsOn(cycleEnd(value, cycleMonths));
  };
  const changeCycle = (months: 1 | 3 | 12 | null) => {
    setCycleMonths(months);
    if (!months) {
      setAutoRenew(false);
      return;
    }
    setEndsOn(cycleEnd(startsOn, months));
    setAutomaticEnd(true);
  };
  const submit = () => {
    const cents = parseCents(amount);
    const missing = [name.trim(), startsOn, endsOn, paidOn].some((value) => !value);
    const invalidDates = endsOn < startsOn || paidOn > today;
    if (cents === null || missing || invalidDates) {
      setError("请填写名称、正数金额（最多两位小数）和有效日期，付款日期不能晚于今天");
      return;
    }
    const body = {
      category: target.category,
      account_id: target.account?.id ?? null,
      name: name.trim(),
      amount_cents: cents,
      currency: "CNY" as const,
      paid_on: paidOn,
      starts_on: startsOn,
      ends_on: endsOn,
      note: note.trim(),
      cycle_months: cycleMonths,
      auto_renew: autoRenew,
    };
    const payload = JSON.stringify(body);
    const id = attempt.current?.payload === payload ? attempt.current.id : crypto.randomUUID();
    attempt.current = { payload, id };
    setError(null);
    mutation.mutate({ ...body, id });
  };
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !mutation.isPending) onClose();
      }}
    >
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {title} · {costLabels[target.category]}
          </DialogTitle>
          <DialogDescription>
            {target.account ? `关联账号：${target.account.name}` : "独立记录资源费用，不分摊到账号"}。
            {target.editing ? "保存后替换原费用，旧记录保留且不重复计费" : "仅录入已确认付款"}
          </DialogDescription>
        </DialogHeader>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (!mutation.isPending) submit();
          }}
          className="space-y-4"
        >
          <fieldset disabled={mutation.isPending} className="space-y-4">
            {target.category === "subscription" && target.account && <CodingPlanPresetPicker onApply={applyPreset} />}
            <div className="space-y-2">
              <Label htmlFor="cost-name">{target.category === "subscription" ? "套餐 / 费用名称" : "资源名称"}</Label>
              <Input id="cost-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={160} required />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label htmlFor="cost-amount">付款金额（人民币 ¥）</Label>
                <Input
                  id="cost-amount"
                  inputMode="decimal"
                  placeholder="0.00"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="cost-paid">付款日期</Label>
                <Input
                  id="cost-paid"
                  type="date"
                  max={today}
                  value={paidOn}
                  onChange={(e) => setPaidOn(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="cost-start">服务开始日期</Label>
                <Input
                  id="cost-start"
                  type="date"
                  value={startsOn}
                  onChange={(e) => changeStart(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="cost-end">服务结束日期</Label>
                <Input
                  id="cost-end"
                  type="date"
                  min={startsOn}
                  value={endsOn}
                  onChange={(e) => {
                    setEndsOn(e.target.value);
                    setAutomaticEnd(false);
                  }}
                  required
                />
              </div>
            </div>
            <div className="space-y-3 rounded-lg border p-3">
              <Label id="cost-cycle-label">计费周期</Label>
              <div role="group" aria-labelledby="cost-cycle-label" className="flex flex-wrap gap-2">
                {(
                  [
                    { months: 1, label: "月付" },
                    { months: 3, label: "季付" },
                    { months: 12, label: "年付" },
                    { months: null, label: "自定义服务期" },
                  ] as const
                )
                  .filter(({ months }) => months !== 3 || target.category === "subscription")
                  .map(({ months, label }) => (
                    <Button
                      key={months ?? "custom"}
                      type="button"
                      variant={cycleMonths === months ? "default" : "outline"}
                      aria-pressed={cycleMonths === months}
                      className="flex-1"
                      onClick={() => changeCycle(months)}
                    >
                      {label}
                    </Button>
                  ))}
              </div>
              {target.category === "subscription" && (
                <>
                  <div className="flex items-center justify-between gap-3">
                    <Label htmlFor="cost-auto-renew">到期后自动续期</Label>
                    <Switch
                      id="cost-auto-renew"
                      checked={autoRenew}
                      disabled={!cycleMonths}
                      onCheckedChange={setAutoRenew}
                    />
                  </div>
                  <p className="text-xs text-muted-foreground">
                    默认不续期。开启后按本次金额和周期自动生成下一期成本，续期日视为付款日，不向厂商扣款。可在账号行关闭
                  </p>
                </>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              结束日默认取下一周期开始日的前一天，月末按目标月最后一天对齐，可手改。服务首尾两天均计入成本。续费会新增记录，请核对日期，避免与已有订阅重复
            </p>
            <div className="space-y-2">
              <Label htmlFor="cost-note">备注（选填）</Label>
              <Textarea id="cost-note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} />
            </div>
          </fieldset>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={mutation.isPending}>
              取消
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? "保存中…" : "保存费用"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
