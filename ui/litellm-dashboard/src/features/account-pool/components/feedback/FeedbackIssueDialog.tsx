"use client";

import { useRef, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
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
import { FEEDBACK_FIELDS, feedbackFormPayload } from "./feedbackFields";

interface Props {
  accessToken: string;
  issue?: FeedbackIssue;
  pagePath?: string;
  onClose: () => void;
  onSaved: () => void;
}

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
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl" aria-describedby={undefined}>
        <DialogHeader><DialogTitle>{t(issue ? "feedback.edit" : "feedback.create")}</DialogTitle></DialogHeader>
        <form onSubmit={save} className="grid gap-4">
          {FEEDBACK_FIELDS.map((field) => (
            <div key={field.name} className="grid gap-2">
              <Label htmlFor={`feedback-${field.name}`}>{t(`feedback.fields.${field.name}`)}</Label>
              {"multiline" in field ? (
                <Textarea id={`feedback-${field.name}`} name={field.name} maxLength={field.maxLength}
                  required={"required" in field && field.required} defaultValue={issue?.[field.name] ?? ""} />
              ) : (
                <Input id={`feedback-${field.name}`} name={field.name} maxLength={field.maxLength}
                  required={"required" in field && field.required}
                  defaultValue={issue?.[field.name] ?? (field.name === "page_path" ? pagePath : "")}
                  pattern={field.name === "page_path" ? "(/[^?#]*)?" : undefined} />
              )}
            </div>
          ))}
          <div className="grid gap-2">
            <Label htmlFor="feedback-priority">{t("feedback.fields.priority")}</Label>
            <Select name="priority" defaultValue={issue?.priority ?? "normal"}>
              <SelectTrigger id="feedback-priority"><SelectValue /></SelectTrigger>
              <SelectContent>{["high", "normal", "low"].map((value) => (
                <SelectItem key={value} value={value}>{t(`feedback.priority.${value}`)}</SelectItem>
              ))}</SelectContent>
            </Select>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="feedback-attachments">{t("feedback.attachments.label")}</Label>
            <Input
              ref={fileInput}
              id="feedback-attachments"
              name="attachments"
              type="file"
              accept=".png,.jpg,.jpeg,.webp,.pdf,.txt,.log"
              multiple
              onChange={(event) => setSelectedFiles(Array.from(event.target.files ?? []))}
            />
            <p className="text-sm text-muted-foreground">{t("feedback.attachments.formats")}</p>
            {selectedFiles.map((file) => <p key={`${file.name}-${file.lastModified}`}>{file.name} ({file.size} B)</p>)}
          </div>
          {error && <p role="alert" className="text-destructive">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" disabled={busy} onClick={onClose}>{t("common.cancel")}</Button>
            <Button type="submit" disabled={busy}>{t("feedback.save")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
