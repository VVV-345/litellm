"use client";

import { useMutation } from "@tanstack/react-query";
import { Plus, Save, Trash2 } from "lucide-react";
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

const emptyDraft = (): MeteredPriceDraft => ({
  providerModelId: "",
  groupId: "manual",
  groupName: "manual",
  currency: "RATIO",
  unit: "multiplier",
  inputPrice: "",
  outputPrice: "",
  cacheReadPrice: "",
  cacheWritePrice: "",
  groupMultiplier: "1",
});

export default function MeteredPriceDialog({
  accessToken,
  channelId,
  initialDrafts,
  expectedOverrideId,
  onClose,
  onSaved,
}: MeteredPriceDialogProps) {
  const [drafts, setDrafts] = useState(initialDrafts.length ? initialDrafts : [emptyDraft()]);
  const [reason, setReason] = useState("");

  const saveMutation = useMutation({
    mutationFn: () =>
      setParserOverride(accessToken, channelId, {
        override_id: generateRequestUuid(),
        target: { kind: "root_field", field: "metered" },
        value: buildMeteredOverrideValue(drafts),
        expected_override_id: expectedOverrideId,
        reason: reason.trim(),
      }),
    onSuccess: async () => {
      NotificationsManager.success("按量价格已保存");
      await onSaved();
      onClose();
    },
    onError: (error) => NotificationsManager.fromBackend(error),
  });

  const updateDraft = (index: number, key: keyof MeteredPriceDraft, value: string) => {
    setDrafts((drafts) => drafts.map((draft, currentIndex) => (currentIndex === index ? { ...draft, [key]: value } : draft)));
  };
  const busy = saveMutation.isPending;
  const canSave = Boolean(reason.trim()) && drafts.every((draft) => draft.providerModelId.trim());

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-6xl">
        <DialogHeader>
          <DialogTitle>补充按量价格</DialogTitle>
          <DialogDescription>填写基础倍率和分组倍率，系统会保存并计算有效倍率</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="overflow-x-auto rounded-md border">
            <table className="w-full min-w-[960px] text-sm">
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
                  <th className="w-12 px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {drafts.map((draft, index) => (
                  <tr key={`${draft.providerModelId}:${index}`} className="border-t">
                    <td className="p-2"><Input value={draft.providerModelId} onChange={(event) => updateDraft(index, "providerModelId", event.target.value)} /></td>
                    <td className="p-2"><Input inputMode="decimal" value={draft.inputPrice} onChange={(event) => updateDraft(index, "inputPrice", event.target.value)} /></td>
                    <td className="p-2"><Input inputMode="decimal" value={draft.outputPrice} onChange={(event) => updateDraft(index, "outputPrice", event.target.value)} /></td>
                    <td className="p-2"><Input inputMode="decimal" value={draft.cacheReadPrice} onChange={(event) => updateDraft(index, "cacheReadPrice", event.target.value)} /></td>
                    <td className="p-2"><Input inputMode="decimal" value={draft.cacheWritePrice} onChange={(event) => updateDraft(index, "cacheWritePrice", event.target.value)} /></td>
                    <td className="p-2"><Input inputMode="decimal" value={draft.groupMultiplier} onChange={(event) => updateDraft(index, "groupMultiplier", event.target.value)} /></td>
                    <td className="p-2"><Input value={draft.currency} onChange={(event) => updateDraft(index, "currency", event.target.value)} /></td>
                    <td className="p-2"><Input value={draft.unit} onChange={(event) => updateDraft(index, "unit", event.target.value)} /></td>
                    <td className="p-2"><Button variant="ghost" size="icon" disabled={busy || drafts.length === 1} onClick={() => setDrafts((drafts) => drafts.filter((_, currentIndex) => currentIndex !== index))}><Trash2 /></Button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Button variant="outline" className="w-fit" disabled={busy} onClick={() => setDrafts((drafts) => [...drafts, emptyDraft()])}>
            <Plus /> 添加模型
          </Button>
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
          <Button variant="outline" onClick={onClose} disabled={busy}>取消</Button>
          <Button onClick={() => saveMutation.mutate()} disabled={busy || !canSave}><Save /> 保存价格</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
