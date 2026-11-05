"use client";

import { useRef, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ChevronRight, Loader2, MessageSquare, Paperclip } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { extractProxyErrorMessage } from "@/lib/http/client";
import {
  createFeedbackIssue,
  editFeedbackIssue,
  uploadFeedbackAttachment,
  type FeedbackIssue,
} from "../../api/AccountPoolFeedbackApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import { feedbackFormPayload } from "./feedbackFields";

interface Props {
  accessToken: string;
  issue?: FeedbackIssue;
  pagePath?: string;
  onClose: () => void;
  onSaved: () => void;
}

const formatBytes = (bytes: number): string => (bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`);

export function FeedbackIssueDialog({ accessToken, issue, pagePath = "", onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [draftIssue, setDraftIssue] = useState(issue);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const body = feedbackFormPayload(new FormData(event.currentTarget));
    if (selectedFiles.some((file) => file.size > 10 * 1024 * 1024)) {
      setError(t("feedback.attachments.tooLarge"));
      return;
    }
    setBusy(true);
    setError("");
    try {
      const savedIssue = draftIssue
        ? await editFeedbackIssue(accessToken, draftIssue.id, { ...body, expected_revision: draftIssue.revision })
        : await createFeedbackIssue(accessToken, body);
      setDraftIssue(savedIssue);
      await selectedFiles.reduce<Promise<FeedbackIssue>>(async (pendingIssue, file) => {
        const currentIssue = await pendingIssue;
        const updatedIssue = await uploadFeedbackAttachment(accessToken, currentIssue.id, currentIssue.revision, file);
        setDraftIssue(updatedIssue);
        setSelectedFiles((files) => files.filter((selected) => selected !== file));
        if (fileInput.current) fileInput.current.value = "";
        return updatedIssue;
      }, Promise.resolve(savedIssue));
      await client.invalidateQueries({ queryKey: accountPoolQueryKeys.feedbackRoot(accessToken) });
      onSaved();
    } catch (failure) {
      setError(extractProxyErrorMessage(failure));
      await client.invalidateQueries({ queryKey: accountPoolQueryKeys.feedbackRoot(accessToken) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="flex max-h-[90dvh] flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl">
        <DialogHeader className="shrink-0 border-b bg-muted/20 px-5 py-4 text-left">
          <DialogTitle className="flex items-center gap-2 pr-6">
            <MessageSquare className="size-5 text-blue-600 dark:text-blue-400" aria-hidden="true" />
            {t(issue ? "feedback.edit" : "feedback.create")}
          </DialogTitle>
          <DialogDescription>{t("feedback.form.intro")}</DialogDescription>
        </DialogHeader>
        <form onSubmit={save} className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <fieldset disabled={busy} className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
            <section className="grid gap-4 rounded-xl border bg-card p-4" aria-label={t("feedback.form.content")}>
              <div className="grid gap-2">
                <Label htmlFor="feedback-title">{t("feedback.fields.title")}</Label>
                <Input
                  id="feedback-title"
                  name="title"
                  maxLength={200}
                  required
                  autoFocus
                  placeholder={t("feedback.form.titlePlaceholder")}
                  defaultValue={issue?.title ?? ""}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="feedback-description">{t("feedback.fields.description")}</Label>
                <Textarea
                  id="feedback-description"
                  name="description"
                  maxLength={8000}
                  rows={4}
                  required
                  placeholder={t("feedback.form.descriptionPlaceholder")}
                  defaultValue={issue?.description ?? ""}
                />
                <p className="text-xs text-muted-foreground">{t("feedback.hints.description")}</p>
              </div>
            </section>
            <details className="group rounded-xl border bg-card" open={issue ? true : undefined}>
              <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
                {t("feedback.form.context")}
                <ChevronRight
                  className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90"
                  aria-hidden="true"
                />
              </summary>
              <div className="grid gap-4 border-t bg-muted/10 p-4">
                <div className="grid gap-2">
                  <Label htmlFor="feedback-steps">{t("feedback.fields.steps")}</Label>
                  <Textarea
                    id="feedback-steps"
                    name="steps"
                    maxLength={8000}
                    rows={3}
                    defaultValue={issue?.steps ?? ""}
                  />
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="grid gap-2">
                    <Label htmlFor="feedback-module">{t("feedback.fields.module")}</Label>
                    <Input id="feedback-module" name="module" maxLength={80} defaultValue={issue?.module ?? ""} />
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="feedback-page_path">{t("feedback.fields.page_path")}</Label>
                    <Input
                      id="feedback-page_path"
                      name="page_path"
                      maxLength={512}
                      pattern="(/[^?#]*)?"
                      defaultValue={issue?.page_path ?? pagePath}
                    />
                  </div>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="grid gap-2">
                    <Label htmlFor="feedback-discovered_version">{t("feedback.fields.discovered_version")}</Label>
                    <Input
                      id="feedback-discovered_version"
                      name="discovered_version"
                      maxLength={160}
                      defaultValue={issue?.discovered_version ?? ""}
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="feedback-priority">{t("feedback.fields.priority")}</Label>
                    <Select name="priority" defaultValue={issue?.priority ?? "normal"} disabled={busy}>
                      <SelectTrigger id="feedback-priority">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {["high", "normal", "low"].map((value) => (
                          <SelectItem key={value} value={value}>
                            {t(`feedback.priority.${value}`)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </div>
            </details>
            <section className="grid gap-2 rounded-xl border border-dashed bg-muted/10 p-4">
              <Label htmlFor="feedback-attachments" className="flex items-center gap-2">
                <Paperclip className="size-4 text-muted-foreground" aria-hidden="true" />
                {t("feedback.attachments.label")}
              </Label>
              <Input
                ref={fileInput}
                id="feedback-attachments"
                name="attachments"
                type="file"
                accept=".png,.jpg,.jpeg,.webp,.pdf,.txt,.log"
                multiple
                onChange={(event) => setSelectedFiles(Array.from(event.target.files ?? []))}
              />
              <p className="text-xs text-muted-foreground">{t("feedback.attachments.formats")}</p>
              {selectedFiles.map((file) => (
                <div
                  key={`${file.name}-${file.lastModified}`}
                  className="flex items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm"
                >
                  <span className="truncate">
                    {file.name} ({formatBytes(file.size)})
                  </span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => setSelectedFiles((files) => files.filter((selected) => selected !== file))}
                  >
                    {t("feedback.attachments.remove")}
                  </Button>
                </div>
              ))}
            </section>
          </fieldset>
          {error && (
            <p
              role="alert"
              className="mx-5 mb-3 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
            >
              {error}
            </p>
          )}
          <div className="flex shrink-0 flex-wrap justify-end gap-2 border-t bg-muted/20 px-5 py-3">
            <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
              {t("feedback.form.cancel")}
            </Button>
            <Button type="submit" disabled={busy}>
              {busy && <Loader2 className="animate-spin" aria-hidden="true" />}
              {t("feedback.save")}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
