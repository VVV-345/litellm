"use client";

import { Loader2, RotateCcw } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

import {
  accountPoolTableColumnDividerClass,
  formatAccountPoolNumber,
  parseOptionalNumber,
} from "../accountPoolPresentation";
import { buildAutoRankingPreview, type AutoRankingPreviewEntry, type AutoRankingSignal } from "../autoRankingPreview";
import type { RoutingPolicyState, RoutingTableEntry } from "../types";

const autoRankingSignalLabels: Record<AutoRankingSignal, string> = {
  latency: "低延迟",
  quota: "高余额",
  cost: "低价格",
};

export function AutoRankingPreviewDialog({
  routes,
  model,
  onClose,
}: {
  routes: RoutingTableEntry[];
  model: string;
  onClose: () => void;
}) {
  const preview = useMemo(() => buildAutoRankingPreview(routes), [routes]);
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[min(720px,calc(100vh-2rem))] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>自动排序建议</DialogTitle>
          <DialogDescription>
            {model} 的建议基于当前健康状态、延迟、余额和可比较价格生成，不会修改正式调度策略或人工顺序
          </DialogDescription>
        </DialogHeader>
        <div className="overflow-hidden rounded-md border">
          <Table className={accountPoolTableColumnDividerClass}>
            <TableHeader>
              <TableRow>
                <TableHead>建议</TableHead>
                <TableHead>渠道</TableHead>
                <TableHead>识别依据</TableHead>
                <TableHead className="text-right">评分</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {preview.map((entry) => (
                <TableRow
                  key={`${entry.route.account_id}:${entry.route.deployment_id}:${entry.route.billing_route_id ?? ""}`}
                >
                  <TableCell className="font-medium tabular-nums">{entry.position}</TableCell>
                  <TableCell>
                    <span className="block font-medium">{entry.route.display_name}</span>
                    <span className="mt-1 block text-xs text-muted-foreground">{entry.route.deployment_id}</span>
                  </TableCell>
                  <TableCell>
                    <AutoRankingEvidence entry={entry} />
                  </TableCell>
                  <TableCell className="text-right font-medium tabular-nums">
                    {entry.score === null ? "-" : formatAccountPoolNumber(entry.score, 0)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AutoRankingEvidence({ entry }: { entry: AutoRankingPreviewEntry }) {
  if (!entry.route.available) {
    return (
      <Badge variant="outline" className="border-red-300 bg-red-50 text-red-800">
        当前不可调度
      </Badge>
    );
  }
  if (entry.signals.length === 0) {
    return <span className="text-xs text-muted-foreground">尚无可用的延迟、余额或价格数据</span>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {entry.signals.map((signal) => (
        <Badge key={signal} variant="outline" className="border-sky-200 bg-sky-50 text-sky-800">
          {autoRankingSignalLabels[signal]}
        </Badge>
      ))}
    </div>
  );
}

interface CandidateDialogProps {
  route: RoutingTableEntry;
  policy: RoutingPolicyState;
  pending: boolean;
  onClose: () => void;
  onSave: (manualOrder: number | null, weight: number | null, paused: boolean) => void;
  onReset: () => void;
}

export function CandidateDialog({ route, policy, pending, onClose, onSave, onReset }: CandidateDialogProps) {
  const existing = policy.overrides.find((override) => override.binding_id === route.binding_id);
  const [manualOrder, setManualOrder] = useState(route.manual_order?.toString() ?? "");
  const [weight, setWeight] = useState(existing?.weight?.toString() ?? "");
  const [paused, setPaused] = useState(route.routing_paused);

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{route.display_name}</DialogTitle>
          <DialogDescription className="break-all">
            {route.account_id} · {route.deployment_id}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="routing-manual-order">人工顺序</Label>
            <Input
              id="routing-manual-order"
              type="number"
              min={0}
              placeholder="自动"
              value={manualOrder}
              onChange={(event) => setManualOrder(event.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="routing-weight">模型权重</Label>
            <Input
              id="routing-weight"
              type="number"
              min={1}
              max={100}
              placeholder="继承渠道"
              value={weight}
              onChange={(event) => setWeight(event.target.value)}
            />
          </div>
          <div className="flex items-center justify-between gap-4 rounded-md border px-3 py-2.5">
            <Label htmlFor="routing-paused">暂停此模型绑定</Label>
            <Switch id="routing-paused" checked={paused} onCheckedChange={setPaused} />
          </div>
        </div>
        <DialogFooter className="sm:justify-between">
          <Button variant="ghost" onClick={onReset} disabled={pending || !existing}>
            <RotateCcw />
            恢复自动
          </Button>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose} disabled={pending}>
              取消
            </Button>
            <Button
              onClick={() => onSave(parseOptionalNumber(manualOrder), parseOptionalNumber(weight), paused)}
              disabled={pending}
            >
              {pending && <Loader2 className="animate-spin" />}
              保存
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
