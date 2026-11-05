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
import { DashboardSection } from "@/components/shared/DashboardSection";
import { extractProxyErrorMessage } from "@/lib/http/client";
import { deleteFeedbackIssue, listFeedbackIssues, type FeedbackFilter, type FeedbackIssue } from "../../api/AccountPoolFeedbackApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import { FEEDBACK_STATUSES } from "./feedbackFields";
import { FeedbackIssueDialog } from "./FeedbackIssueDialog";
import { FeedbackDetailDialog } from "./FeedbackDetailDialog";
import { FeedbackTokens } from "./FeedbackTokens";

const PAGE_SIZE = 50;

export function AccountPoolFeedbackPanel({ accessToken, active = true }: { accessToken: string; active?: boolean }) {
  const { t, i18n } = useTranslation();
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
  const statusOptions = (["unresolved", "all", ...FEEDBACK_STATUSES] as const).map((value) => ({
    value, label: t(`feedback.status.${value}`),
  }));
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
      <DashboardSection title={t("feedback.title")} actions={<>
          <Button variant="outline" onClick={() => void issues.refetch()}>{t("feedback.refresh")}</Button>
          <Button variant="outline" onClick={() => setTokensOpen(true)}>{t("feedback.tokens.title")}</Button>
          <Button onClick={() => setEditing("create")}>{t("feedback.create")}</Button>
        </>}>
      <div className="mb-4 flex flex-wrap gap-2" aria-label={t("feedback.stats")}>
        {FEEDBACK_STATUSES.map((value) => <Button variant={status === value ? "secondary" : "outline"} size="sm" key={value}
          aria-pressed={status === value} onClick={() => changeStatus(value)}>
          {t(`feedback.status.${value}`)}<Badge variant="secondary" className="ml-1 tabular-nums">{issues.data?.stats[value] ?? "-"}</Badge>
        </Button>)}
      </div>
      <form onSubmit={searchIssues} className="flex flex-wrap items-end gap-3 rounded-lg border bg-muted/10 p-3">
        <div className="grid gap-2"><Label htmlFor="feedback-filter">{t("feedback.filter")}</Label>
          <Select items={statusOptions} value={status} onValueChange={changeStatus}>
            <SelectTrigger id="feedback-filter"><SelectValue /></SelectTrigger>
            <SelectContent>{statusOptions.map((option) => (
              <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
            ))}</SelectContent>
          </Select></div>
        <div className="grid min-w-0 flex-1 gap-2 sm:min-w-56"><Label htmlFor="feedback-search">{t("feedback.searchLabel")}</Label>
          <Input id="feedback-search" name="q" maxLength={200} /></div>
        <Button variant="outline" type="submit">{t("feedback.search")}</Button>
      </form>
      </DashboardSection>
      <DashboardSection title={t("nativeWorkspace.results")} summary={t("nativeWorkspace.pageSummary", { total: issues.data?.total ?? 0, count: issues.data?.items.length ?? 0 })}>
      {issues.error && <p role="alert" className="text-destructive">{extractProxyErrorMessage(issues.error)}</p>}
      {issues.isPending && <p role="status">{t("common.loading")}</p>}
      <Table>
        <TableHeader><TableRow>{["title", "status", "priority", "module", "updated_at", "operations"].map((name) => <TableHead key={name}>{t(`feedback.fields.${name}`)}</TableHead>)}</TableRow></TableHeader>
        <TableBody>{issues.data?.items.map((issue) => (
          <TableRow key={issue.id}>
            <TableCell className="min-w-52 max-w-md">
              <Button variant="link" className="h-auto max-w-full justify-start whitespace-normal px-0 text-left" onClick={() => setDetail(issue)}>{issue.title}</Button>
              <p className="line-clamp-1 text-xs text-muted-foreground">{issue.description}</p>
            </TableCell>
            <TableCell><Badge variant={issue.status === "resolved" ? "secondary" : "outline"}>{t(`feedback.status.${issue.status}`)}</Badge></TableCell>
            <TableCell><Badge variant={issue.priority === "high" ? "destructive" : "secondary"}>{t(`feedback.priority.${issue.priority}`)}</Badge></TableCell>
            <TableCell className="text-sm text-muted-foreground">{issue.module || "-"}</TableCell>
            <TableCell className="whitespace-nowrap text-xs text-muted-foreground"><time dateTime={issue.updated_at} title={issue.updated_at}>
              {new Date(issue.updated_at).toLocaleString(i18n.language, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}
            </time></TableCell>
            <TableCell><div className="flex gap-2">
              <Button variant="outline" size="sm" aria-label={t("feedback.editNamed", { title: issue.title })} onClick={() => setEditing(issue)}>{t("feedback.edit")}</Button>
              <Button variant="outline" size="sm" aria-label={t("feedback.deleteNamed", { title: issue.title })} onClick={() => openDelete(issue)}>{t("feedback.delete")}</Button>
            </div></TableCell>
          </TableRow>
        ))}</TableBody>
      </Table>
      {issues.data?.items.length === 0 && <p>{t("feedback.empty")}</p>}
      <div className="mt-3 flex flex-wrap items-center justify-end gap-3 border-t pt-3 text-sm">
        <span>{t("feedback.total", { total: issues.data?.total ?? 0, page: Math.floor(offset / PAGE_SIZE) + 1 })}</span>
        <Button variant="outline" disabled={offset === 0 || issues.isFetching} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>{t("feedback.previous")}</Button>
        <Button variant="outline" disabled={!issues.data || offset + PAGE_SIZE >= issues.data.total || issues.isFetching} onClick={() => setOffset(offset + PAGE_SIZE)}>{t("feedback.next")}</Button>
      </div>
      </DashboardSection>
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
