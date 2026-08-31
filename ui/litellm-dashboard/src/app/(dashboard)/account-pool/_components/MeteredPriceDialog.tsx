// 本文件仅允许为最近一次解析发现的模型补充按量价格。
"use client";

import { useMutation } from "@tanstack/react-query";
import { Save } from "lucide-react";
import { useState } from "react";

import NotificationsManager from "@/components/molecules/notifications_manager";
import { generateRequestUuid } from "@/lib/uuid";
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

import { setParserOverride } from "../api";
import { buildMeteredOverrideValue, type MeteredPriceDraft } from "../meteredPriceEditor";

interface MeteredPriceDialogProps {
  accessToken: string;
  channelId: string;
  initialDrafts: MeteredPriceDraft[];
  expectedOverrideId: string | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}

export default function MeteredPriceDialog({
  accessToken,
  channelId,
  initialDrafts,
  expectedOverrideId,
  onClose,
  onSaved,
}: MeteredPriceDialogProps) {
  const [drafts, setDrafts] = useState(initialDrafts);
  const [reason, setReason] = useState("");

  const saveMutation = useMutation({
    mutationFn: () => {
      const request = {
        override_id: generateRequestUuid(),
        target: { kind: "root_field", field: "metered" },
        value: buildMeteredOverrideValue(
          drafts,
          initialDrafts.map((draft) => draft.providerModelId),
        ),
        expected_override_id: expectedOverrideId,
        reason: reason.trim(),
      } as const;
      return setParserOverride(accessToken, channelId, request);
    },
    onSuccess: async () => {
      NotificationsManager.success("按量价格已保存");
      await onSaved();
      onClose();
    },
    onError: (error) => NotificationsManager.fromBackend(error),
  });

  const updateDraft = (index: number, key: Exclude<keyof MeteredPriceDraft, "providerModelId">, value: string) => {
    setDrafts((drafts) =>
      drafts.map((draft, currentIndex) => (currentIndex === index ? { ...draft, [key]: value } : draft)),
    );
  };
  const busy = saveMutation.isPending;
  const pricedDrafts = drafts.filter((draft) => draft.inputPrice.trim() || draft.outputPrice.trim());
  const hasPartialPrice = drafts.some(
    (draft) => Boolean(draft.inputPrice.trim()) !== Boolean(draft.outputPrice.trim()),
  );
  const canSave = Boolean(reason.trim()) && pricedDrafts.length > 0 && !hasPartialPrice;

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-6xl">
        <DialogHeader>
          <DialogTitle>补充按量价格</DialogTitle>
          <DialogDescription>填写每个已发现模型的价格</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          {drafts.length === 0 ? (
            <p className="text-sm text-muted-foreground">未找到最近一次解析发现的模型</p>
          ) : (
            <div className="overflow-x-auto rounded-md border">
              <table className="w-full min-w-[920px] text-sm">
                <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2">模型</th>
                    <th className="px-3 py-2">输入</th>
                    <th className="px-3 py-2">输出</th>
                    <th className="px-3 py-2">缓存读</th>
                    <th className="px-3 py-2">缓存写</th>
                    <th className="px-3 py-2">分组倍率</th>
                    <th className="px-3 py-2">币种</th>
                    <th className="px-3 py-2">单位</th>
                  </tr>
                </thead>
                <tbody>
                  {drafts.map((draft, index) => (
                    <tr key={draft.providerModelId} className="border-t">
                      <td className="p-2 font-medium break-all">{draft.providerModelId}</td>
                      <td className="p-2">
                        <Input
                          inputMode="decimal"
                          value={draft.inputPrice}
                          onChange={(event) => updateDraft(index, "inputPrice", event.target.value)}
                        />
                      </td>
                      <td className="p-2">
                        <Input
                          inputMode="decimal"
                          value={draft.outputPrice}
                          onChange={(event) => updateDraft(index, "outputPrice", event.target.value)}
                        />
                      </td>
                      <td className="p-2">
                        <Input
                          inputMode="decimal"
                          value={draft.cacheReadPrice}
                          onChange={(event) => updateDraft(index, "cacheReadPrice", event.target.value)}
                        />
                      </td>
                      <td className="p-2">
                        <Input
                          inputMode="decimal"
                          value={draft.cacheWritePrice}
                          onChange={(event) => updateDraft(index, "cacheWritePrice", event.target.value)}
                        />
                      </td>
                      <td className="p-2">
                        <Input
                          inputMode="decimal"
                          value={draft.groupMultiplier}
                          onChange={(event) => updateDraft(index, "groupMultiplier", event.target.value)}
                        />
                      </td>
                      <td className="p-2">
                        <Input
                          value={draft.currency}
                          onChange={(event) => updateDraft(index, "currency", event.target.value)}
                        />
                      </td>
                      <td className="p-2">
                        <Input
                          value={draft.unit}
                          onChange={(event) => updateDraft(index, "unit", event.target.value)}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="grid gap-2">
            <Label htmlFor="metered-price-reason">修改原因</Label>
            <Input
              id="metered-price-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="记录价格来源或人工核对原因"
              maxLength={1000}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button onClick={() => saveMutation.mutate()} disabled={busy || !canSave}>
            <Save /> 保存价格
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
