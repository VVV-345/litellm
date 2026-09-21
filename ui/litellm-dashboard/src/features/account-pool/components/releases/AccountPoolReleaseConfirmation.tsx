/** 本文件展示服务器签发的操作内容与倒计时，倒计时结束仍需手动确认。 */
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { ReleaseConfirmation } from "../../api/AccountPoolReleasesApi";
import { AccountPoolRollbackReport } from "./AccountPoolRollbackReport";

function confirmationStatus({
  busy,
  stale,
  allowed,
  expired,
  remaining,
}: {
  busy: boolean;
  stale: boolean;
  allowed: boolean;
  expired: boolean;
  remaining: number;
}) {
  if (busy) return "正在处理，请稍候…";
  if (stale) return "版本或备份已变化，请取消后重新检查";
  if (!allowed) return "检查尚未通过，无法确认回退";
  if (expired) return "确认已过期，请取消后重新操作";
  return remaining > 0 ? `请核对操作，${remaining} 秒后可以确认` : "已结束等待，请确认是否执行";
}

function confirmationLabel(busy: boolean, allowed: boolean, remaining: number) {
  if (busy) return "正在处理…";
  if (!allowed) return "暂不可回退";
  return remaining > 0 ? `确认（${remaining} 秒）` : "确认执行";
}

export function AccountPoolReleaseConfirmation({
  confirmation,
  title,
  description,
  before,
  busy,
  onConfirm,
  onClose,
  onSelectVersion,
  onForce,
  stale = false,
}: {
  confirmation: ReleaseConfirmation;
  title: string;
  description: string;
  before?: string;
  busy: boolean;
  onConfirm: () => void;
  onClose: () => void;
  onSelectVersion?: (id: string) => void;
  onForce?: () => void;
  stale?: boolean;
}) {
  const [openedAt] = useState(Date.now);
  const heading = useRef<HTMLHeadingElement>(null);
  const [now, setNow] = useState(Date.now);
  const [forceText, setForceText] = useState("");
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(timer);
  }, []);
  const remaining = Math.max(0, Math.ceil((openedAt + confirmation.delay_seconds * 1000 - now) / 1000));
  const expired = now >= openedAt + (confirmation.expires_in_seconds ?? 300) * 1000;
  const editing = confirmation.action.action === "note" || confirmation.action.action === "guide";
  const rollback = confirmation.action.action === "apply";
  const forced = confirmation.action.force === true;
  const forceAvailable = confirmation.rollback?.force_allowed === true;
  const forceAcknowledged = forceText === confirmation.rollback?.target_commit.slice(0, 10);
  const allowed = !rollback || (!!confirmation.token && (forced ? forceAvailable : confirmation.rollback?.status === "compatible"));
  const unavailable = remaining > 0 || expired || stale || !allowed;
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <DialogContent
        className={rollback ? "flex max-h-[90dvh] flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl" : "sm:max-w-xl"}
        showCloseButton={!busy}
        initialFocus={rollback ? heading : undefined}
      >
        <DialogHeader className={rollback ? "shrink-0 border-b px-5 py-5 pr-12 sm:px-6" : ""}>
          <DialogTitle ref={heading} tabIndex={-1} className="outline-none">
            {title}
          </DialogTitle>
          <DialogDescription className="break-words">{description}</DialogDescription>
        </DialogHeader>
        {rollback && (
          <div className="min-h-0 flex-1 overflow-y-auto p-5 sm:p-6">
            {confirmation.rollback ? (
              <AccountPoolRollbackReport
                report={confirmation.rollback}
                busy={busy}
                onSelect={onSelectVersion ?? (() => {})}
              />
            ) : (
              <p role="alert">部署服务未返回回退检查结果，请更新部署服务后重新检查。</p>
            )}
          </div>
        )}
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
        <div className={rollback ? "shrink-0 space-y-3 border-t bg-muted/20 px-5 py-4 sm:px-6" : "space-y-3"}>
          {rollback && forceAvailable && !forced && (
            <div className="space-y-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-800 dark:bg-amber-950">
              <p className="font-medium">可选择强制回退</p>
              <p>跳过未验证的兼容项，旧程序可能无法正常使用当前数据。仅切换程序，不恢复数据库或认证文件。</p>
              <label htmlFor="rollback-force-version">输入目标版本 {confirmation.rollback?.target_commit.slice(0, 10)}，再进入强制确认</label>
              <Input id="rollback-force-version" value={forceText} onChange={(event) => setForceText(event.target.value)} autoComplete="off" />
              <Button variant="destructive" disabled={!forceAcknowledged || busy || stale || expired || !onForce} onClick={onForce}>
                进入强制回退确认
              </Button>
            </div>
          )}
          {forced && <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm">当前为强制回退：上述未验证项不会阻止切换。若启动失败会尝试切回原程序，不恢复数据快照。</p>}
          <p role="status" className="text-sm text-muted-foreground">
            {confirmationStatus({ busy, stale, allowed, expired, remaining })}
          </p>
          <DialogFooter>
            <Button variant="outline" disabled={busy} onClick={onClose}>
              取消
            </Button>
            <Button
              variant={confirmation.action.action === "delete" || forced ? "destructive" : "default"}
              disabled={unavailable || busy}
              onClick={onConfirm}
            >
              {forced && allowed && remaining === 0 && !busy ? "确认强制回退" : confirmationLabel(busy, allowed, remaining)}
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  );
}
