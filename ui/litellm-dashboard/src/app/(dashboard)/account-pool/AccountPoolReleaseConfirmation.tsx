/** 本文件展示服务器签发的操作内容与倒计时，倒计时结束仍需手动确认。 */
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { ReleaseConfirmation } from "./AccountPoolReleasesApi";

export function AccountPoolReleaseConfirmation({
  confirmation,
  title,
  description,
  before,
  busy,
  onConfirm,
  onClose,
}: {
  confirmation: ReleaseConfirmation;
  title: string;
  description: string;
  before?: string;
  busy: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const [openedAt] = useState(Date.now);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(timer);
  }, []);
  const remaining = Math.max(0, Math.ceil((openedAt + confirmation.delay_seconds * 1000 - now) / 1000));
  const expired = now >= openedAt + (confirmation.expires_in_seconds ?? 300) * 1000;
  const editing = confirmation.action.action === "note" || confirmation.action.action === "guide";
  const waitingText = remaining > 0 ? `请核对操作，${remaining} 秒后可以确认` : "已结束等待，请确认是否执行";
  const buttonText = remaining > 0 ? `确认（${remaining} 秒）` : "确认执行";
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <DialogContent className="sm:max-w-xl" showCloseButton={!busy}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription className="break-words">{description}</DialogDescription>
        </DialogHeader>
        {editing && (
          <div className="grid max-h-72 gap-3 overflow-auto text-sm sm:grid-cols-2">
            <div>
              <p className="mb-2 font-medium">修改前</p>
              <pre className="whitespace-pre-wrap break-words rounded-lg bg-muted p-3 font-sans">{before || "空"}</pre>
            </div>
            <div>
              <p className="mb-2 font-medium">修改后</p>
              <pre className="whitespace-pre-wrap break-words rounded-lg bg-muted p-3 font-sans">
                {confirmation.action.text || "空"}
              </pre>
            </div>
          </div>
        )}
        <p role="status" className="text-sm text-muted-foreground">
          {expired ? "确认已过期，请取消后重新操作" : waitingText}
        </p>
        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={onClose}>
            取消
          </Button>
          <Button
            variant={confirmation.action.action === "delete" ? "destructive" : "default"}
            disabled={remaining > 0 || expired || busy}
            onClick={onConfirm}
          >
            {busy ? "正在提交…" : buttonText}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
