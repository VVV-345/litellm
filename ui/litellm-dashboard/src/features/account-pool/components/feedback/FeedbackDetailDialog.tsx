"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Download } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { extractProxyErrorMessage } from "@/lib/http/client";
import {
  downloadFeedbackAttachment,
  getFeedbackIssue,
  mutateFeedbackIssue,
  type FeedbackIssue,
  type FeedbackMutation,
} from "../../api/AccountPoolFeedbackApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import { feedbackActions } from "./feedbackFields";

export function FeedbackDetailDialog({ accessToken, initialIssue, onClose }: {
  accessToken: string; initialIssue: FeedbackIssue; onClose: () => void;
}) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [issue, setIssue] = useState(initialIssue);
  const [selection, setSelection] = useState<{ status: FeedbackIssue["status"]; action: FeedbackMutation["action"] }>({
    status: initialIssue.status, action: feedbackActions(initialIssue.status)[0],
  });
  const action = selection.status === issue.status ? selection.action : feedbackActions(issue.status)[0];
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let mounted = true;
    void getFeedbackIssue(accessToken, initialIssue.id).then(
      (fresh) => {
        if (mounted) setIssue((current) => fresh.revision > current.revision ? fresh : current);
      },
      (failure) => {
        if (mounted) setError(extractProxyErrorMessage(failure));
      },
    );
    return () => { mounted = false; };
  }, [accessToken, initialIssue.id]);
  const mutate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    setError("");
    try {
      const updated = await mutateFeedbackIssue(accessToken, issue.id, {
        action, expected_revision: issue.revision,
        summary: String(data.get("summary") ?? ""), commit: String(data.get("commit") ?? ""),
        verification: String(data.get("verification") ?? ""), deployed_version: String(data.get("deployed_version") ?? ""),
      });
      setIssue(updated);
      form.reset();
      await client.invalidateQueries({ queryKey: accountPoolQueryKeys.feedbackRoot(accessToken) });
    } catch (failure) {
      setError(extractProxyErrorMessage(failure));
    } finally {
      setBusy(false);
    }
  };
  const downloadAttachment = async (attachmentId: string, filename: string) => {
    setError("");
    try {
      const blob = await downloadFeedbackAttachment(accessToken, issue.id, attachmentId);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (failure) {
      setError(extractProxyErrorMessage(failure));
    }
  };
  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl" aria-describedby={undefined}>
        <DialogHeader><DialogTitle>{issue.title}</DialogTitle></DialogHeader>
        <div className="flex gap-2"><Badge>{t(`feedback.status.${issue.status}`)}</Badge><span>{t("feedback.revision", { revision: issue.revision })}</span></div>
        <dl className="grid gap-3 text-sm">
          {(["description", "steps", "module", "page_path", "discovered_version", "assigned_to", "resolution", "commit", "verification", "deployed_version", "created_at", "updated_at"] as const).map((name) => (
            <div key={name}><dt className="font-medium">{t(`feedback.fields.${name}`)}</dt><dd className="whitespace-pre-wrap break-words">{issue[name] || "-"}</dd></div>
          ))}
        </dl>
        {issue.attachments.length > 0 && (
          <section className="grid gap-2">
            <h3 className="font-medium">{t("feedback.attachments.title")}</h3>
            <ul className="grid gap-2">
              {issue.attachments.map((attachment) => (
                <li key={attachment.id} className="flex items-center justify-between gap-3 text-sm">
                  <span className="break-all">{attachment.filename} ({attachment.size} B)</span>
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    aria-label={t("feedback.attachments.downloadNamed", { name: attachment.filename })}
                    title={t("feedback.attachments.download")}
                    onClick={() => void downloadAttachment(attachment.id, attachment.filename)}
                  >
                    <Download aria-hidden="true" size={16} />
                  </Button>
                </li>
              ))}
            </ul>
          </section>
        )}
        <section className="grid gap-2">
          <h3 className="font-medium">{t("feedback.history")}</h3>
          {issue.events.map((event, index) => (
            <div key={`${event.at}-${index}`} className="rounded-md border p-3 text-sm whitespace-pre-wrap break-words">
              <p>{event.at} · {event.actor} · {t(`feedback.actions.${event.action}`)}</p>
              {[event.summary, event.commit, event.verification, event.deployed_version].filter(Boolean).map((value, i) => <p key={i}>{value}</p>)}
            </div>
          ))}
        </section>
        <form onSubmit={mutate} className="grid gap-3 border-t pt-4">
          <div className="flex flex-wrap gap-2" role="group" aria-label={t("feedback.action")}>
            {feedbackActions(issue.status).map((value) => (
              <Button key={value} type="button" variant={action === value ? "default" : "outline"}
                aria-pressed={action === value} disabled={busy} onClick={() => setSelection({ status: issue.status, action: value })}>{t(`feedback.actions.${value}`)}</Button>
            ))}
          </div>
          <Label htmlFor="feedback-summary">{t("feedback.fields.summary")}</Label>
          <Textarea id="feedback-summary" name="summary" maxLength={8000} required={action === "submit_fix"} />
          <Label htmlFor="feedback-commit">{t("feedback.fields.commit")}</Label>
          <Input id="feedback-commit" name="commit" maxLength={64} required={action === "submit_fix"} pattern={action === "submit_fix" ? "[0-9a-fA-F]{7,64}" : undefined} />
          <Label htmlFor="feedback-verification">{t("feedback.fields.verification")}</Label>
          <Textarea id="feedback-verification" name="verification" maxLength={16000} required={action === "submit_fix" || action === "resolve"} />
          <Label htmlFor="feedback-deployed-version">{t("feedback.fields.deployed_version")}</Label>
          <Input id="feedback-deployed-version" name="deployed_version" maxLength={160} required={action === "resolve"} />
          {error && <p role="alert" className="text-destructive">{error}</p>}
          <Button type="submit" disabled={busy}>{t("feedback.applyAction", { action: t(`feedback.actions.${action}`) })}</Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
