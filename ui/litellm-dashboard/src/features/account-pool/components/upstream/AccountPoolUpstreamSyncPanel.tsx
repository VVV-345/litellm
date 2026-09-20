/** 本文件展示 CLIProxyAPI 上游同步状态，并调度隔离检测、Codex 审查包和正式合并。 */

import { useMutation, useQuery, type UseQueryOptions } from "@tanstack/react-query";
import { Download, ExternalLink, GitMerge, RefreshCw, SearchCheck } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/lib/toast";

import {
  analyzeAccountPoolUpstream,
  getAccountPoolCodexReview,
  getAccountPoolUpstreamSync,
  promoteAccountPoolUpstream,
  type UpstreamSyncDispatch,
  type UpstreamSyncView,
} from "../../api/AccountPoolManagementApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";

interface AccountPoolUpstreamSyncPanelProps {
  accessToken: string;
}

const isPendingRequest = (status: UpstreamSyncView | undefined, requestId: string | null) =>
  requestId !== null && status?.report.request_id !== requestId;

const stateBadgeVariant = (state: UpstreamSyncView["report"]["state"] | "queued") => {
  if (state === "passed" || state === "promoted") return "secondary" as const;
  if (state === "failed" || state === "conflict") return "destructive" as const;
  return "outline" as const;
};

const downloadReview = (filename: string, content: string) => {
  const url = URL.createObjectURL(new Blob([content], { type: "text/markdown;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
};

export function AccountPoolUpstreamSyncPanel({ accessToken }: AccountPoolUpstreamSyncPanelProps) {
  const { t } = useTranslation();
  const [pendingRequestId, setPendingRequestId] = useState<string | null>(null);
  const [promotionOpen, setPromotionOpen] = useState(false);
  const statusQueryOptions: UseQueryOptions<UpstreamSyncView> = {
    queryKey: accountPoolQueryKeys.upstreamSync(accessToken),
    queryFn: () => getAccountPoolUpstreamSync(accessToken),
    retry: false,
    refetchInterval: (query) => (isPendingRequest(query.state.data, pendingRequestId) ? 10_000 : false),
  };
  const statusQuery = useQuery(statusQueryOptions);

  const startTracking = (dispatch: UpstreamSyncDispatch) => {
    setPendingRequestId(dispatch.request_id);
    void statusQuery.refetch();
  };
  const analyzeMutation = useMutation({
    mutationFn: () => analyzeAccountPoolUpstream(accessToken),
    onSuccess: (dispatch) => {
      startTracking(dispatch);
      toast.success(t("accountPool.upstreamSync.analysisQueued"));
    },
    onError: (error: Error) => toast.fromError(error),
  });
  const promoteMutation = useMutation({
    mutationFn: () => promoteAccountPoolUpstream(accessToken),
    onSuccess: (dispatch) => {
      setPromotionOpen(false);
      startTracking(dispatch);
      toast.success(t("accountPool.upstreamSync.promotionQueued"));
    },
    onError: (error: Error) => toast.fromError(error),
  });
  const reviewMutation = useMutation({
    mutationFn: () => getAccountPoolCodexReview(accessToken),
    onSuccess: (review) => {
      downloadReview(review.filename, review.content);
      toast.success(t("accountPool.upstreamSync.reviewDownloaded"));
    },
    onError: (error: Error) => toast.fromError(error),
  });

  if (statusQuery.isPending) {
    return <Skeleton className="h-72 w-full" />;
  }
  if (statusQuery.isError || !statusQuery.data) {
    return (
      <Card>
        <CardContent className="flex items-center justify-between gap-4 p-6" role="alert">
          <p className="text-sm text-destructive">{t("accountPool.upstreamSync.loadFailed")}</p>
          <Button type="button" variant="outline" onClick={() => void statusQuery.refetch()}>
            {t("accountPool.retry")}
          </Button>
        </CardContent>
      </Card>
    );
  }

  const status = statusQuery.data;
  const report = status.report;
  const waitingForReport = isPendingRequest(status, pendingRequestId);
  const displayedState = waitingForReport ? "queued" : report.state;
  const busy = analyzeMutation.isPending || promoteMutation.isPending || waitingForReport;
  const promotable = report.state === "passed" && report.target_tag === status.latest_tag;

  return (
    <div className="grid gap-5" data-testid="account-pool-upstream-sync">
      <div>
        <h2 className="text-lg font-semibold">{t("accountPool.upstreamSync.title")}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.upstreamSync.description")}</p>
      </div>
      <VersionStatusCard
        status={status}
        displayedState={displayedState}
        busy={busy}
        fetching={statusQuery.isFetching}
        reviewPending={reviewMutation.isPending}
        promotable={promotable}
        onRefresh={() => void statusQuery.refetch()}
        onAnalyze={() => analyzeMutation.mutate()}
        onReview={() => reviewMutation.mutate()}
        onPromote={() => setPromotionOpen(true)}
      />
      <CompatibilityReportCard status={status} displayedState={displayedState} waitingForReport={waitingForReport} />
      <PromotionDialog
        open={promotionOpen}
        targetTag={status.latest_tag}
        pending={promoteMutation.isPending}
        onOpenChange={setPromotionOpen}
        onConfirm={() => promoteMutation.mutate()}
      />
    </div>
  );
}

interface VersionStatusCardProps {
  status: UpstreamSyncView;
  displayedState: UpstreamSyncView["report"]["state"];
  busy: boolean;
  fetching: boolean;
  reviewPending: boolean;
  promotable: boolean;
  onRefresh: () => void;
  onAnalyze: () => void;
  onReview: () => void;
  onPromote: () => void;
}

function VersionStatusCard({
  status,
  displayedState,
  busy,
  fetching,
  reviewPending,
  promotable,
  onRefresh,
  onAnalyze,
  onReview,
  onPromote,
}: VersionStatusCardProps) {
  const { t } = useTranslation();
  const reviewAvailable = status.report.state !== "idle";
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <CardTitle className="text-base">{t("accountPool.upstreamSync.versionStatus")}</CardTitle>
          <Badge variant={status.update_available ? "default" : "outline"}>
            {t(
              status.update_available
                ? "accountPool.upstreamSync.updateAvailable"
                : "accountPool.upstreamSync.upToDate",
            )}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <VersionValue label={t("accountPool.upstreamSync.currentVersion")} value={status.current_tag} />
          <VersionValue label={t("accountPool.upstreamSync.latestVersion")} value={status.latest_tag} />
          <VersionValue label={t("accountPool.upstreamSync.fixedBranch")} value={status.sync_branch} />
          <VersionValue
            label={t("accountPool.upstreamSync.reportState")}
            value={t(`accountPool.upstreamSync.states.${displayedState}`)}
          />
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" onClick={onRefresh} disabled={fetching || busy}>
            <RefreshCw className={fetching ? "animate-spin" : undefined} />
            {t("accountPool.upstreamSync.checkUpdates")}
          </Button>
          <Button
            type="button"
            onClick={onAnalyze}
            disabled={!status.update_available || !status.dispatch_configured || busy}
          >
            <SearchCheck />
            {t("accountPool.upstreamSync.analyze")}
          </Button>
          <Button type="button" variant="outline" onClick={onReview} disabled={!reviewAvailable || reviewPending}>
            <Download />
            {t("accountPool.upstreamSync.codexReview")}
          </Button>
          <Button
            type="button"
            variant="destructive"
            onClick={onPromote}
            disabled={!promotable || !status.dispatch_configured || busy}
          >
            <GitMerge />
            {t("accountPool.upstreamSync.promote")}
          </Button>
        </div>
        {!status.dispatch_configured && (
          <p className="text-sm text-amber-700 dark:text-amber-400" role="status">
            {t("accountPool.upstreamSync.authorizationMissing")}
          </p>
        )}
        <p className="text-xs text-muted-foreground">{t("accountPool.upstreamSync.safetyNote")}</p>
      </CardContent>
    </Card>
  );
}

interface CompatibilityReportCardProps {
  status: UpstreamSyncView;
  displayedState: UpstreamSyncView["report"]["state"];
  waitingForReport: boolean;
}

function CompatibilityReportCard({ status, displayedState, waitingForReport }: CompatibilityReportCardProps) {
  const { t } = useTranslation();
  const report = status.report;
  const reportDescription = waitingForReport
    ? t("accountPool.upstreamSync.waitingForReport")
    : t(`accountPool.upstreamSync.stateDescriptions.${report.state}`);
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <CardTitle className="text-base">{t("accountPool.upstreamSync.compatibilityReport")}</CardTitle>
          <Badge variant={stateBadgeVariant(displayedState)}>
            {t(`accountPool.upstreamSync.states.${displayedState}`)}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm">
        <p>{reportDescription}</p>
        <div className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
          <span>{t("accountPool.upstreamSync.targetTag", { tag: report.target_tag ?? "-" })}</span>
          <span>
            {t("accountPool.upstreamSync.updatedAt", {
              time: report.updated_at ? new Date(report.updated_at).toLocaleString() : "-",
            })}
          </span>
          <span>{t("accountPool.upstreamSync.baseCommit", { sha: report.base_sha?.slice(0, 12) ?? "-" })}</span>
          <span>
            {t("accountPool.upstreamSync.candidateCommit", { sha: report.candidate_sha?.slice(0, 12) ?? "-" })}
          </span>
        </div>
        {report.conflict_files.length > 0 && (
          <ReportList title={t("accountPool.upstreamSync.conflicts")} items={report.conflict_files} />
        )}
        {report.failed_steps.length > 0 && (
          <ReportList title={t("accountPool.upstreamSync.failedSteps")} items={report.failed_steps} />
        )}
        <div className="flex flex-wrap gap-4 text-xs">
          <ExternalLinkItem href={status.latest_release_url} label={t("accountPool.upstreamSync.openRelease")} />
          {report.workflow_url && (
            <ExternalLinkItem href={report.workflow_url} label={t("accountPool.upstreamSync.openWorkflow")} />
          )}
        </div>
      </CardContent>
    </Card>
  );
}

interface PromotionDialogProps {
  open: boolean;
  targetTag: string;
  pending: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}

function PromotionDialog({ open, targetTag, pending, onOpenChange, onConfirm }: PromotionDialogProps) {
  const { t } = useTranslation();
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("accountPool.upstreamSync.confirmPromotionTitle")}</AlertDialogTitle>
          <AlertDialogDescription>
            {t("accountPool.upstreamSync.confirmPromotionDescription", { tag: targetTag })}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>{t("accountPool.cancel")}</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={pending} onClick={onConfirm}>
            {t("accountPool.upstreamSync.confirmPromotion")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function ExternalLinkItem({ href, label }: { href: string; label: string }) {
  return (
    <a
      className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline"
      href={href}
      target="_blank"
      rel="noreferrer"
    >
      {label}
      <ExternalLink className="size-3" />
    </a>
  );
}

function VersionValue({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 break-all font-mono text-sm font-medium">{value}</p>
    </div>
  );
}

function ReportList({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3">
      <p className="font-medium text-destructive">{title}</p>
      <ul className="mt-2 list-inside list-disc font-mono text-xs">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
