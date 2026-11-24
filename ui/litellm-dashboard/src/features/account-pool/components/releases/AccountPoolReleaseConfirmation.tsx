/** 本文件展示服务器签发的操作内容与倒计时，倒计时结束仍需手动确认。 */
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
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

function confirmationStatus(t: TFunction, {
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
  if (busy) return t("accountPool.releaseConfirm.processingPleaseWait");
  if (stale) return t("accountPool.releaseConfirm.theVersionOrBackupHasChanged");
  if (!allowed) return t("accountPool.releaseConfirm.checksHaveNotPassedRollbackCannot");
  if (expired) return t("accountPool.releaseConfirm.confirmationExpiredCancelAndStartAgain");
  return remaining > 0 ? t("accountPool.releaseConfirm.reviewTheOperationYouCanConfirm", { seconds: remaining }) : t("accountPool.releaseConfirm.theWaitingPeriodIsOverConfirm");
}

function confirmationLabel(t: TFunction, busy: boolean, allowed: boolean, remaining: number) {
  if (busy) return t("accountPool.releaseConfirm.processing");
  if (!allowed) return t("accountPool.releaseConfirm.rollbackUnavailable");
  return remaining > 0 ? t("accountPool.releaseConfirm.confirmS", { seconds: remaining }) : t("accountPool.releaseConfirm.confirmOperation");
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
  onConfirm: (acknowledgement?: string) => void;
  onClose: () => void;
  onSelectVersion?: (id: string) => void;
  onForce?: () => void;
  stale?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const [openedAt] = useState(Date.now);
  const heading = useRef<HTMLHeadingElement>(null);
  const [now, setNow] = useState(Date.now);
  const [forceText, setForceText] = useState("");
  const countdownEnd = openedAt + confirmation.delay_seconds * 1000;
  const expiresAt = openedAt + (confirmation.expires_in_seconds ?? 300) * 1000;
  const remaining = Math.max(0, Math.ceil((countdownEnd - now) / 1000));
  const expired = now >= expiresAt;
  const stillCounting = remaining > 0;
  useEffect(() => {
    if (expired) return;
    if (stillCounting) {
      const timer = setInterval(() => setNow(Date.now()), 200);
      return () => clearInterval(timer);
    }
    const timer = setTimeout(() => setNow(Date.now()), Math.max(0, expiresAt - Date.now()));
    return () => clearTimeout(timer);
  }, [expired, expiresAt, stillCounting]);
  const editing = confirmation.action.action === "note" || confirmation.action.action === "guide";
  const rollback = confirmation.action.action === "apply";
  const forced = confirmation.action.force === true;
  const restoresData = confirmation.action.action === "restore_data";
  const dataPhrase = `RESTORE ${confirmation.database_snapshot?.id ?? ""}`;
  const forceAvailable = confirmation.rollback?.force_allowed === true;
  const forceAcknowledged = forceText === confirmation.rollback?.target_commit.slice(0, 10);
  const allowed = restoresData
    ? !!confirmation.token && !!confirmation.database_snapshot && forceText === dataPhrase
    : !rollback || (!!confirmation.token && (forced ? forceAvailable : confirmation.rollback?.status === "compatible"));
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
        {restoresData && (
          <div className="space-y-3 text-sm">
            <p role="alert" className="text-destructive">{t("accountPool.releaseConfirm.restoreWarning", { time: confirmation.database_snapshot ? new Date(confirmation.database_snapshot.created_at * 1000).toLocaleString(i18n.language) : t("accountPool.releaseConfirm.anUnknownTime") })}</p>
            <p className="break-all">{t("accountPool.releaseConfirm.snapshot")}{confirmation.database_snapshot?.id}</p>
            <label htmlFor="database-restore-confirm" className="block break-all">{t("accountPool.releaseConfirm.confirmSnapshot", { phrase: dataPhrase })}</label>
            <Input id="database-restore-confirm" value={forceText} onChange={(event) => setForceText(event.target.value)} autoComplete="off" />
          </div>
        )}
        {rollback && (
          <div className="min-h-0 flex-1 overflow-y-auto p-5 sm:p-6">
            {confirmation.rollback ? (
              <AccountPoolRollbackReport
                report={confirmation.rollback}
                busy={busy}
                onSelect={onSelectVersion ?? (() => {})}
              />
            ) : (
              <p role="alert">{t("accountPool.releaseConfirm.theDeploymentServiceReturnedNoRollback")}</p>
            )}
          </div>
        )}
        {editing && (
          <div className="grid max-h-72 gap-3 overflow-auto text-sm sm:grid-cols-2">
            <div>
              <p className="mb-2 font-medium">{t("accountPool.releaseConfirm.before")}</p>
              <pre className="whitespace-pre-wrap break-words rounded-lg bg-muted p-3 font-sans">{before || t("accountPool.releaseConfirm.empty")}</pre>
            </div>
            <div>
              <p className="mb-2 font-medium">{t("accountPool.releaseConfirm.after")}</p>
              <pre className="whitespace-pre-wrap break-words rounded-lg bg-muted p-3 font-sans">
                {confirmation.action.text || t("accountPool.releaseConfirm.empty")}
              </pre>
            </div>
          </div>
        )}
        <div className={rollback ? "shrink-0 space-y-3 border-t bg-muted/20 px-5 py-4 sm:px-6" : "space-y-3"}>
          {rollback && forceAvailable && !forced && (
            <div className="space-y-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-800 dark:bg-amber-950">
              <p className="font-medium">{t("accountPool.releaseConfirm.forceRollbackIsAvailable")}</p>
              <p>{t("accountPool.releaseConfirm.skipUnverifiedCompatibilityChecksTheOld")}</p>
              <label htmlFor="rollback-force-version">{t("accountPool.releaseConfirm.confirmTarget", { version: confirmation.rollback?.target_commit.slice(0, 10) })}</label>
              <Input id="rollback-force-version" value={forceText} onChange={(event) => setForceText(event.target.value)} autoComplete="off" />
              <Button variant="destructive" disabled={!forceAcknowledged || busy || stale || expired || !onForce} onClick={onForce}>
                {t("accountPool.releaseConfirm.reviewForcedRollback")}</Button>
            </div>
          )}
          {forced && <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm">{t("accountPool.releaseConfirm.forcedRollbackTheUnverifiedItemsAbove")}</p>}
          <p role="status" className="text-sm text-muted-foreground">
            {confirmationStatus(t, { busy, stale, allowed, expired, remaining })}
          </p>
          <DialogFooter>
            <Button variant="outline" disabled={busy} onClick={onClose}>
              {t("accountPool.onboarding.cleanup.cancel")}</Button>
            <Button
              variant={confirmation.action.action === "delete" || forced || restoresData ? "destructive" : "default"}
              disabled={unavailable || busy}
              onClick={() => onConfirm(restoresData ? forceText : undefined)}
            >
              {forced && allowed && remaining === 0 && !busy ? t("accountPool.releaseConfirm.confirmForcedRollback") : confirmationLabel(t, busy, allowed, remaining)}
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  );
}
