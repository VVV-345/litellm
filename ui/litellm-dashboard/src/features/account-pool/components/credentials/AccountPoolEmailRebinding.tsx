/** 独立换绑入口：只在明确确认后修改账号，任务失败保留原卡和目标邮箱占用。 */
"use client";

import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import dynamic from "next/dynamic";
import { useId, useRef, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { extractProxyErrorMessage } from "@/lib/http/client";
import {
  actOnEmailBinding,
  confirmEmailBinding,
  exportEmailBindingStatus,
  getEmailBindings,
  openEmailBindingScreen,
  proposeEmailBinding,
  provideEmailBindingCode,
  uploadEmailPool,
  type BindingView,
} from "../../api/AccountPoolEmailRebindingApi";
import { createAccountPoolJobId, getAccountPoolOAuthBrowserUrl } from "../../api/AccountPoolManagementApi";
import { getAutomation } from "../../api/AccountPoolAutomationApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import type { AccountPoolEnvironment } from "../../utils/AccountPoolTypes";

function mailboxLabel(t: TFunction, status: string) {
  if (status === "bound") return t("accountPool.automation.bound");
  if (status === "reserved") return t("accountPool.mailboxChange.inProgressUnavailableForReassignment");
  return t("accountPool.mailboxChange.unused");
}
function retryLabel(t: TFunction, changed: boolean, stage: string) {
  if (changed) return t("accountPool.mailboxChange.retryAuthorizationAndVerificationOnly");
  return stage === "waiting_code" ? t("accountPool.mailboxChange.retryReceivingEmail") : t("accountPool.mailboxChange.retryBrowserSetup");
}

const Scripts = dynamic(() =>
  import("../onboarding/AccountPoolLoginScriptSettings").then((m) => m.AccountPoolLoginScriptSettings),
);
const Authorization = dynamic(() =>
  import("../onboarding/AccountPoolAutomationPanel").then((m) => m.AccountPoolAutomationPanel),
);

export function AccountPoolEmailRebinding({
  accessToken,
  environment,
}: {
  accessToken: string | null;
  environment: AccountPoolEnvironment;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={!accessToken}
        onClick={() => setOpen(true)}
        aria-label={t("accountPool.mailboxChange.changeEmailFor", { name: environment.name })}
      >
        {t("accountPool.mailboxChange.changeEmail")}</Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>{t("accountPool.mailboxChange.changeEmailDetail")} {environment.name}</DialogTitle>
            <DialogDescription>
              {t("accountPool.mailboxChange.completeOauthBeforePreparingAnEmail")}</DialogDescription>
          </DialogHeader>
          {open && accessToken && <BindingPanel accessToken={accessToken} environment={environment} />}
        </DialogContent>
      </Dialog>
    </>
  );
}

export function BindingPanel({
  accessToken,
  environment,
}: {
  accessToken: string;
  environment: AccountPoolEnvironment;
}) {
  const { t } = useTranslation();
  const id = useId();
  const file = useRef<HTMLInputElement>(null);
  const client = useQueryClient();
  const key = accountPoolQueryKeys.emailRebinding(accessToken);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [target, setTarget] = useState("");
  const [scripts, setScripts] = useState(false);
  const [screen, setScreen] = useState<string | null>(null);
  const query = useQuery({
    queryKey: key,
    queryFn: () => getEmailBindings(accessToken),
    retry: false,
    refetchInterval: (q) =>
      q.state.data?.jobs.some((j) => !["completed", "cancelled", "failed", "uncertain"].includes(j.stage))
        ? 2000
        : false,
  });
  const jobs = query.data?.jobs.filter((j) => j.environment_id === environment.id) ?? [];
  const job = jobs.at(-1);
  const pending = job && !["completed", "cancelled"].includes(job.stage);
  const available = query.data?.mailboxes.filter((m) => m.status === "available") ?? [];
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await action();
      await client.invalidateQueries({ queryKey: key });
      await client.invalidateQueries({ queryKey: accountPoolQueryKeys.environments(accessToken) });
    } catch (failure) {
      setError(extractProxyErrorMessage(failure));
    } finally {
      setBusy(false);
    }
  };
  const importFile = async () => {
    const selected = file.current?.files?.[0];
    if (!selected) return;
    if (selected.size > 1048576) {
      setError(t("accountPool.mailboxChange.theFileMustBe1Mib"));
      return;
    }
    await run(async () => {
      const result = await uploadEmailPool(accessToken, await selected.text(), selected.name);
      setNotice(t("accountPool.mailboxChange.addedAlreadyExistedExistingAssignmentsAnd", { added: result.added, existing: result.existing }));
      if (file.current) file.current.value = "";
    });
  };
  return (
    <div className="space-y-4">
      <section className="space-y-2 rounded-lg border p-3">
        <Label htmlFor={`${id}-file`}>{t("accountPool.mailboxChange.uploadTargetMailboxesCsvTxt")}</Label>
        <Input id={`${id}-file`} ref={file} type="file" accept=".csv,.txt,text/plain,text/csv" disabled={busy} />
        <p className="text-xs text-muted-foreground">
          {t("accountPool.mailboxChange.useEmailPasswordClientIdRefresh")}</p>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" disabled={busy} onClick={() => void importFile()}>
            {t("accountPool.mailboxChange.importMailboxes")}</Button>
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                const blob = await exportEmailBindingStatus(accessToken);
                const url = URL.createObjectURL(blob);
                const anchor = document.createElement("a");
                anchor.href = url;
                anchor.download = "mailbox-binding-status.csv";
                anchor.click();
                URL.revokeObjectURL(url);
              })
            }
          >
            {t("accountPool.mailboxChange.downloadBindingStatusCsv")}</Button>
        </div>
        {notice && (
          <p role="status" className="text-sm">
            {notice}
          </p>
        )}
        <p className="text-xs text-muted-foreground">
          {t("accountPool.mailboxChange.yourOriginalFileIsNotModified")}</p>
      </section>
      {query.isError && <p role="alert">{t("accountPool.mailboxChange.couldNotLoadMailboxesCheckYour")}</p>}
      <section className="space-y-2">
        <Label htmlFor={`${id}-target`}>{t("accountPool.mailboxChange.availableMailboxes", { total: available.length })}</Label>
        <select
          id={`${id}-target`}
          className="h-9 w-full rounded-md border bg-background px-3 text-sm"
          value={target}
          disabled={busy || Boolean(pending)}
          onChange={(e) => setTarget(e.target.value)}
        >
          <option value="">{t("accountPool.mailboxChange.allocateAnUnusedMailbox")}</option>
          {available.map((m) => (
            <option key={m.id} value={m.id}>
              {m.email}
            </option>
          ))}
        </select>
        <Button
          type="button"
          disabled={busy || Boolean(pending) || !available.length || environment.status !== "ready"}
          onClick={() =>
            void run(async () => {
              await proposeEmailBinding(accessToken, createAccountPoolJobId(), environment.id, target || undefined);
            })
          }
        >
          {t("accountPool.mailboxChange.reviewDetailsBeforeChangingEmail")}</Button>
      </section>
      {job && (
        <BindingProgress
          key={job.id}
          accessToken={accessToken}
          job={job}
          busy={busy}
          run={run}
          setScreen={setScreen}
          mailboxStatus={query.data?.mailboxes.find((m) => m.id === job.mailbox_id)?.status}
        />
      )}
      <details>
        <summary>{t("accountPool.mailboxChange.mailboxAssignments")}</summary>
        <ul className="max-h-48 overflow-auto text-sm">
          {query.data?.mailboxes.map((m) => (
            <li key={m.id}>
              {m.email} · {mailboxLabel(t, m.status)}
            </li>
          ))}
        </ul>
      </details>
      <Button type="button" variant="ghost" onClick={() => setScripts(!scripts)}>
        {t("accountPool.mailboxChange.emailChangeScriptSettings")}</Button>
      {scripts && <Scripts accessToken={accessToken} kind="rebinding" />}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <Dialog open={screen !== null} onOpenChange={(value) => !value && setScreen(null)}>
        <DialogContent className="h-[85vh] w-[95vw] max-w-6xl">
          <DialogHeader>
            <DialogTitle>{t("accountPool.mailboxChange.emailChangeBrowser")}</DialogTitle>
            <DialogDescription>{t("accountPool.mailboxChange.viewingDoesNotEnableInputAfter")}</DialogDescription>
          </DialogHeader>
          {screen && (
            <iframe
              title={t("accountPool.mailboxChange.emailChangeBrowserView")}
              src={getAccountPoolOAuthBrowserUrl(screen)}
              className="h-full w-full border-0"
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function BindingProgress({
  accessToken,
  job,
  busy,
  run,
  setScreen,
  mailboxStatus,
}: {
  accessToken: string;
  job: BindingView;
  busy: boolean;
  run: (action: () => Promise<unknown>) => Promise<void>;
  setScreen: (id: string) => void;
  mailboxStatus?: string;
}) {
  const { t } = useTranslation();
  const id = useId();
  const code = useRef<HTMLInputElement>(null);
  const [ack, setAck] = useState(false);
  const authorized = useQuery({
    queryKey: accountPoolQueryKeys.automation(accessToken),
    queryFn: () => getAutomation(accessToken),
    enabled: Boolean(job?.oauth_job_id),
    refetchInterval:
      job?.oauth_job_id && !["completed", "cancelled", "failed", "uncertain"].includes(job.stage) ? 2000 : false,
  });
  const oauthJob = authorized.data?.jobs.find((j) => j.id === job?.oauth_job_id);
  const submitCode = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = code.current?.value ?? "";
    if (code.current) code.current.value = "";
    if (job) void run(() => provideEmailBindingCode(accessToken, job.id, value));
  };
  return (
    <section className="space-y-3 rounded-lg border p-3" aria-label={t("accountPool.mailboxChange.emailChangeProgress")}>
      <p className="break-all text-sm">
        {t("accountPool.mailboxChange.originalEmail")}{job.old_email}
        <br />
        {t("accountPool.mailboxChange.targetEmail")}{job.target_email}
      </p>
      <p role="status" className="text-sm">
        {job.message}
      </p>
      <p className="text-xs text-muted-foreground">
        {t("accountPool.mailboxChange.targetMailboxStatus")}{mailboxStatus === "bound" ? t("accountPool.automation.bound") : t("accountPool.mailboxChange.reserved")}{t("accountPool.mailboxChange.submittedChangesAreNotRepeatedAnd")}</p>
      {job.stage === "awaiting_confirmation" && (
        <>
          <p className="text-sm">
            {t("accountPool.mailboxChange.confirmingPausesNewRequestsOnThis")}</p>
          <div className="flex items-center gap-2">
            <Checkbox id={`${id}-ack`} checked={ack} onCheckedChange={(v) => setAck(v === true)} />
            <Label htmlFor={`${id}-ack`}>
              {t("accountPool.mailboxChange.confirmAddresses", { old: job.old_email, target: job.target_email })}
            </Label>
          </div>
          <Button
            type="button"
            disabled={busy || !ack}
            onClick={() => void run(() => confirmEmailBinding(accessToken, job))}
          >
            {t("accountPool.mailboxChange.confirmAndChangeEmail")}</Button>
        </>
      )}
      <div className="flex flex-wrap gap-2">
        {!job.irreversible_started && !["completed", "cancelled"].includes(job.stage) && (
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => void run(() => actOnEmailBinding(accessToken, job.id, "cancel"))}
          >
            {t("accountPool.mailboxChange.cancelAndReleaseUnusedMailbox")}</Button>
        )}
        {["failed", "verification_failed", "waiting_code"].includes(job.stage) && (
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => void run(() => actOnEmailBinding(accessToken, job.id, "retry"))}
          >
            {retryLabel(t, job.email_changed, job.stage)}
          </Button>
        )}
        {job.irreversible_started && !job.email_changed && (
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => void run(() => actOnEmailBinding(accessToken, job.id, "reconcile"))}
          >
            {t("accountPool.mailboxChange.checkTheOutcomeWithoutRepeatingThe")}</Button>
        )}
        {job.irreversible_started && ["uncertain", "failed", "verification_failed"].includes(job.stage) && (
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => void run(() => actOnEmailBinding(accessToken, job.id, "abort"))}
          >
            {t("accountPool.mailboxChange.forceReleaseTheCard")}</Button>
        )}
        {job.browser_session_id && !job.email_changed && (
          <>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  setScreen((await openEmailBindingScreen(accessToken, job.id)).id);
                })
              }
            >
              {t("accountPool.mailboxChange.viewBrowser")}</Button>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => void run(() => actOnEmailBinding(accessToken, job.id, "takeover"))}
            >
              {t("accountPool.automation.takeover")}</Button>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => void run(() => actOnEmailBinding(accessToken, job.id, "resume"))}
            >
              {t("accountPool.mailboxChange.returnControlToAutomation")}</Button>
          </>
        )}
      </div>
      {job.stage === "waiting_code" && (
        <form className="flex items-end gap-2" onSubmit={submitCode}>
          <div>
            <Label htmlFor={`${id}-code`}>{t("accountPool.mailboxChange.targetMailboxVerificationCode")}</Label>
            <Input id={`${id}-code`} ref={code} autoComplete="off" pattern="[0-9]{6}" maxLength={6} required />
          </div>
          <Button type="submit" disabled={busy}>
            {t("accountPool.mailboxChange.submitVerificationCode")}</Button>
        </form>
      )}
      {oauthJob && <Authorization accessToken={accessToken} reauthorizingJob={oauthJob} />}
    </section>
  );
}
