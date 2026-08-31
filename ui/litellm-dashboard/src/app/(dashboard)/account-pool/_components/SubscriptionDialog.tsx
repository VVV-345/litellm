// 本文件提供套餐覆盖模型、余量和渠道并发的结构化人工补充界面。
"use client";

import { useMutation } from "@tanstack/react-query";
import { Save } from "lucide-react";
import { useState } from "react";

import NotificationsManager from "@/components/molecules/notifications_manager";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { generateRequestUuid } from "@/lib/uuid";

import { setParserOverride } from "../api";
import { buildSubscriptionOverrideValue, type SubscriptionDraft } from "../subscriptionEditor";

interface SubscriptionDialogProps {
  accessToken: string;
  channelId: string;
  models: string[];
  initialDraft: SubscriptionDraft;
  expectedOverrideId: string | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}

export default function SubscriptionDialog({
  accessToken,
  channelId,
  models,
  initialDraft,
  expectedOverrideId,
  onClose,
  onSaved,
}: SubscriptionDialogProps) {
  const [draft, setDraft] = useState(initialDraft);
  const [reason, setReason] = useState("");
  const saveMutation = useMutation({
    mutationFn: () => {
      const request = {
        override_id: generateRequestUuid(),
        target: { kind: "root_field", field: "subscription" },
        value: buildSubscriptionOverrideValue(draft, models),
        expected_override_id: expectedOverrideId,
        reason: reason.trim(),
      } as const;
      return setParserOverride(accessToken, channelId, request);
    },
    onSuccess: async () => {
      NotificationsManager.success("套餐信息已保存");
      await onSaved();
      onClose();
    },
    onError: (error) => NotificationsManager.fromBackend(error),
  });
  const selected = new Set(draft.selectedModels);
  const hasSubscriptionSelection = draft.selectedModels.length > 0;
  const hasRemainingUsage = Boolean(draft.remainingUsage.trim());
  const hasUsageUnit = Boolean(draft.usageUnit.trim());
  const hasRequiredData = hasSubscriptionSelection && hasRemainingUsage && hasUsageUnit;
  const canSave = Boolean(reason.trim()) && hasRequiredData;
  const toggleModel = (model: string, checked: boolean) => {
    setDraft((current) => ({
      ...current,
      selectedModels: checked
        ? [...current.selectedModels, model]
        : current.selectedModels.filter((candidate) => candidate !== model),
    }));
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>补充套餐信息</DialogTitle>
          <DialogDescription>仅可选择最近一次解析发现的资源侧模型</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="subscription-plan-name">套餐名称</Label>
            <Input
              id="subscription-plan-name"
              value={draft.planName}
              onChange={(event) => setDraft((current) => ({ ...current, planName: event.target.value }))}
              placeholder="可选"
            />
          </div>
          <div className="grid gap-2">
            <div className="flex items-center justify-between gap-3">
              <Label>套餐覆盖模型</Label>
              <span className="text-xs text-muted-foreground">
                已选择 {draft.selectedModels.length} / {models.length}
              </span>
            </div>
            <div className="max-h-56 divide-y overflow-y-auto rounded-md border">
              {models.map((model, index) => (
                <label
                  key={model}
                  htmlFor={`subscription-model-${index}`}
                  className="flex cursor-pointer items-center gap-3 px-3 py-2 text-sm hover:bg-muted/50"
                >
                  <Checkbox
                    id={`subscription-model-${index}`}
                    checked={selected.has(model)}
                    onCheckedChange={(checked) => toggleModel(model, checked === true)}
                  />
                  <span className="min-w-0 break-all">{model}</span>
                </label>
              ))}
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="grid gap-2">
              <Label htmlFor="subscription-remaining-usage">剩余用量</Label>
              <Input
                id="subscription-remaining-usage"
                inputMode="decimal"
                value={draft.remainingUsage}
                onChange={(event) => setDraft((current) => ({ ...current, remainingUsage: event.target.value }))}
                placeholder="例如 500"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="subscription-usage-unit">用量单位</Label>
              <Input
                id="subscription-usage-unit"
                value={draft.usageUnit}
                onChange={(event) => setDraft((current) => ({ ...current, usageUnit: event.target.value }))}
                placeholder="次、积分或 tokens"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="subscription-channel-concurrency">渠道并发</Label>
              <Input
                id="subscription-channel-concurrency"
                type="number"
                min={1}
                value={draft.channelConcurrency}
                onChange={(event) => setDraft((current) => ({ ...current, channelConcurrency: event.target.value }))}
                placeholder="10"
              />
            </div>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="subscription-reason">修改原因</Label>
            <Input
              id="subscription-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="记录套餐来源或人工核对原因"
              maxLength={1000}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saveMutation.isPending}>
            取消
          </Button>
          <Button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending || !canSave}>
            <Save />
            保存套餐
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
