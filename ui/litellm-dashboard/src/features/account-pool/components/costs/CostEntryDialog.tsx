/** 真实账号订阅及独立资源费用的单条录入，保存失败保留幂等编号。 */
import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import { createCost, type CostCategory, type CostCreate, type CostRecord } from "../../api/AccountPoolCostsApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import { chinaToday, costLabels, nextDate, parseCents } from "./costForm";
import { CodingPlanPresetPicker } from "./CodingPlanPresetPicker";

export type CostEntryTarget = { category: CostCategory; account?: { id: string; name: string }; renewal?: CostRecord };
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
  const [name, setName] = useState(target.renewal?.name ?? (target.account ? `${target.account.name} 订阅` : ""));
  const [amount, setAmount] = useState(target.renewal ? (target.renewal.amount_cents / 100).toFixed(2) : "");
  const [paidOn, setPaidOn] = useState(today);
  const [startsOn, setStartsOn] = useState(target.renewal ? nextDate(target.renewal.ends_on) : today);
  const [endsOn, setEndsOn] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const attempt = useRef<{ payload: string; id: string } | null>(null);
  const mutation = useMutation({
    mutationFn: (body: CostCreate) => createCost(accessToken, body),
    onSuccess: async () => {
      await cache.invalidateQueries({ queryKey: accountPoolQueryKeys.costsRoot(accessToken) });
      toast.success("费用已录入");
      onClose();
    },
    onError: (cause: Error) => setError(cause.message || "保存失败，请重试"),
  });
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
            {target.renewal ? "记录续费" : "录入费用"} · {costLabels[target.category]}
          </DialogTitle>
          <DialogDescription>
            {target.account ? `关联账号：${target.account.name}` : "独立记录资源费用，不分摊到账号"}。仅录入已确认付款
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
            {target.category === "subscription" && target.account && (
              <CodingPlanPresetPicker
                onApply={(presetName, presetAmount) => {
                  setName(presetName);
                  setAmount(presetAmount);
                }}
              />
            )}
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
                  onChange={(e) => setStartsOn(e.target.value)}
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
                  onChange={(e) => setEndsOn(e.target.value)}
                  required
                />
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              服务首尾两天均计入成本。续费会新增记录，请核对日期，避免与已有订阅重复
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
