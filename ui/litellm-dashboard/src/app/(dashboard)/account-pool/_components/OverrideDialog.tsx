// 本文件提供解析字段的 JSON 人工修正和已有覆盖撤销交互。
"use client";

import { useMutation } from "@tanstack/react-query";
import { RotateCcw, Save } from "lucide-react";
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
import { Textarea } from "@/components/ui/textarea";

import { revokeParserOverride, setParserOverride } from "../api";
import { formatJsonValue, type ParserFieldRow } from "../parserRows";
import type { JsonValue } from "../types";

interface OverrideDialogProps {
  accessToken: string;
  channelId: string;
  row: ParserFieldRow;
  onClose: () => void;
  onSaved: () => Promise<void>;
}

export default function OverrideDialog({ accessToken, channelId, row, onClose, onSaved }: OverrideDialogProps) {
  const [valueText, setValueText] = useState(formatJsonValue(row.effectiveValue));
  const [reason, setReason] = useState("");

  const saveMutation = useMutation({
    mutationFn: async () => {
      const value = JSON.parse(valueText) as JsonValue;
      return setParserOverride(accessToken, channelId, {
        override_id: generateRequestUuid(),
        target: row.target,
        value,
        expected_override_id: row.activeOverrideId,
        reason: reason.trim(),
      });
    },
    onSuccess: async () => {
      NotificationsManager.success("人工修正已保存");
      await onSaved();
      onClose();
    },
    onError: (error) => NotificationsManager.fromBackend(error),
  });

  const revokeMutation = useMutation({
    mutationFn: () =>
      revokeParserOverride(accessToken, channelId, row.path, {
        override_id: generateRequestUuid(),
        expected_override_id: row.activeOverrideId!,
        reason: reason.trim(),
      }),
    onSuccess: async () => {
      NotificationsManager.success("人工修正已撤销");
      await onSaved();
      onClose();
    },
    onError: (error) => NotificationsManager.fromBackend(error),
  });

  const busy = saveMutation.isPending || revokeMutation.isPending;
  const reasonMissing = reason.trim().length === 0;

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>修正 {row.label}</DialogTitle>
          <DialogDescription>
            字段路径 <code className="font-mono text-foreground">{row.path}</code>，保存后优先于自动解析值
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="override-value">有效 JSON 值</Label>
            <Textarea
              id="override-value"
              className="min-h-52 font-mono text-xs"
              value={valueText}
              onChange={(event) => setValueText(event.target.value)}
              spellCheck={false}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="override-reason">修改原因</Label>
            <Input
              id="override-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="记录为什么需要人工修正"
              maxLength={1000}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            取消
          </Button>
          {row.activeOverrideId && (
            <Button variant="destructive" onClick={() => revokeMutation.mutate()} disabled={busy || reasonMissing}>
              <RotateCcw />
              撤销修正
            </Button>
          )}
          <Button onClick={() => saveMutation.mutate()} disabled={busy || reasonMissing}>
            <Save />
            保存修正
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
