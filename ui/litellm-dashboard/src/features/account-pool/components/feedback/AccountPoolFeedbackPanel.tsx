"use client";

import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { extractProxyErrorMessage } from "@/lib/http/client";
import { deleteFeedbackIssue, listFeedbackIssues, type FeedbackFilter, type FeedbackIssue } from "../../api/AccountPoolFeedbackApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import { FEEDBACK_STATUSES } from "./feedbackFields";
import { FeedbackIssueDialog } from "./FeedbackIssueDialog";
import { FeedbackDetailDialog } from "./FeedbackDetailDialog";
import { FeedbackTokens } from "./FeedbackTokens";

const PAGE_SIZE = 50;

export function AccountPoolFeedbackPanel({ accessToken, active = true }: { accessToken: string; active?: boolean }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [status, setStatus] = useState<FeedbackFilter>("unresolved");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState<FeedbackIssue | "create" | null>(null);
  const [detail, setDetail] = useState<FeedbackIssue | null>(null);
  const [deleting, setDeleting] = useState<FeedbackIssue | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [tokensOpen, setTokensOpen] = useState(false);
  const filter = { status, q: search, limit: PAGE_SIZE, offset };
  const issues = useQuery({
    queryKey: accountPoolQueryKeys.feedback(accessToken, filter),
    queryFn: () => listFeedbackIssues(accessToken, filter),
    enabled: active, retry: false,
  });
  const searchIssues = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSearch(String(new FormData(event.currentTarget).get("q") ?? "").trim());
    setOffset(0);
  };
  const changeStatus = (value: FeedbackFilter | null) => {
    if (!value) return;
    setStatus(value);
    setOffset(0);
  };
  const confirmDelete = async () => {
    if (!deleting) return;
    setDeleteBusy(true);
    setDeleteError("");
    try {
      await deleteFeedbackIssue(accessToken, deleting.id, deleting.revision);
      setDeleting(null);
      await client.invalidateQueries({ queryKey: accountPoolQueryKeys.feedbackRoot(accessToken) });
    } catch (failure) {
      setDeleteError(extractProxyErrorMessage(failure));
    } finally {
      setDeleteBusy(false);
    }
  };
  const openDelete = (issue: FeedbackIssue) => {
    setDeleteError("");
    setDeleting(issue);
  };
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">{t("feedback.title")}</h2>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => void issues.refetch()}>{t("feedback.refresh")}</Button>
          <Button variant="outline" onClick={() => setTokensOpen(true)}>{t("feedback.tokens.title")}</Button>
          <Button onClick={() => setEditing("create")}>{t("feedback.create")}</Button>
        </div>
      </div>
      <div className="flex flex-wrap gap-3" aria-label={t("feedback.stats")}>
        {FEEDBACK_STATUSES.map((value) => <Badge variant="outline" key={value}>{t(`feedback.status.${value}`)}：{issues.data?.stats[value] ?? "-"}</Badge>)}
      </div>
      <form onSubmit={searchIssues} className="flex flex-wrap items-end gap-3">
        <div className="grid gap-2"><Label htmlFor="feedback-filter">{t("feedback.filter")}</Label>
          <Select value={status} onValueChange={changeStatus}>
            <SelectTrigger id="feedback-filter"><SelectValue /></SelectTrigger>
            <SelectContent>{(["unresolved", "all", ...FEEDBACK_STATUSES] as const).map((value) => (
              <SelectItem key={value} value={value}>{t(`feedback.status.${value}`)}</SelectItem>
            ))}</SelectContent>
          </Select></div>
        <div className="grid gap-2"><Label htmlFor="feedback-search">{t("feedback.searchLabel")}</Label>
          <Input id="feedback-search" name="q" maxLength={200} /></div>
        <Button variant="outline" type="submit">{t("feedback.search")}</Button>
      </form>
      {issues.error && <p role="alert" className="text-destructive">{extractProxyErrorMessage(issues.error)}</p>}
      {issues.isPending && <p role="status">{t("common.loading")}</p>}
      <Table>
        <TableHeader><TableRow>{["title", "status", "priority", "module", "updated_at", "operations"].map((name) => <TableHead key={name}>{t(`feedback.fields.${name}`)}</TableHead>)}</TableRow></TableHeader>
        <TableBody>{issues.data?.items.map((issue) => (
          <TableRow key={issue.id}>
            <TableCell><Button variant="link" onClick={() => setDetail(issue)}>{issue.title}</Button></TableCell>
            <TableCell>{t(`feedback.status.${issue.status}`)}</TableCell>
            <TableCell>{t(`feedback.priority.${issue.priority}`)}</TableCell>
            <TableCell>{issue.module || "-"}</TableCell><TableCell>{issue.updated_at}</TableCell>
            <TableCell><div className="flex gap-2">
              <Button variant="outline" size="sm" aria-label={t("feedback.editNamed", { title: issue.title })} onClick={() => setEditing(issue)}>{t("feedback.edit")}</Button>
              <Button variant="outline" size="sm" aria-label={t("feedback.deleteNamed", { title: issue.title })} onClick={() => openDelete(issue)}>{t("feedback.delete")}</Button>
            </div></TableCell>
          </TableRow>
        ))}</TableBody>
      </Table>
      {issues.data?.items.length === 0 && <p>{t("feedback.empty")}</p>}
      <div className="flex items-center justify-end gap-3">
        <span>{t("feedback.total", { total: issues.data?.total ?? 0, page: Math.floor(offset / PAGE_SIZE) + 1 })}</span>
        <Button variant="outline" disabled={offset === 0 || issues.isFetching} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>{t("feedback.previous")}</Button>
        <Button variant="outline" disabled={!issues.data || offset + PAGE_SIZE >= issues.data.total || issues.isFetching} onClick={() => setOffset(offset + PAGE_SIZE)}>{t("feedback.next")}</Button>
      </div>
      {active && tokensOpen && <FeedbackTokens key={accessToken} accessToken={accessToken} onClose={() => setTokensOpen(false)} />}
      {active && editing && <FeedbackIssueDialog accessToken={accessToken} issue={editing === "create" ? undefined : editing}
        pagePath={window.location.pathname} onClose={() => setEditing(null)} onSaved={() => setEditing(null)} />}
      {active && detail && <FeedbackDetailDialog key={detail.id} accessToken={accessToken} initialIssue={detail} onClose={() => setDetail(null)} />}
      <Dialog open={active && deleting !== null} onOpenChange={(open) => !open && !deleteBusy && setDeleting(null)}>
        <DialogContent aria-describedby={undefined}>
          <DialogHeader><DialogTitle>{t("feedback.deleteTitle")}</DialogTitle></DialogHeader>
          <p>{deleting?.title}</p>
          {deleteError && <p role="alert" className="text-destructive">{deleteError}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="outline" disabled={deleteBusy} onClick={() => setDeleting(null)}>{t("common.cancel")}</Button>
            <Button variant="destructive" disabled={deleteBusy} onClick={() => void confirmDelete()}>{t("feedback.confirmDelete")}</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
