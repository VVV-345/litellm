/** 本文件分别展示 LiteLLM 与 CLIProxyAPI 上游状态，调度检测、审查包和各自的更新操作。 */

import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";
import { Download, ExternalLink, GitMerge, RefreshCw, SearchCheck } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { z } from "zod";

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
import { ApiError } from "@/lib/http/client";
import { toast } from "@/lib/toast";
import { UPSTREAM_SYNC_STATUS_CACHE_PREFIX } from "@/lib/cacheEvents";
import { getRequestBaseUrl } from "@/lib/http/runtime";

import {
  analyzeAccountPoolUpstream,
  analyzeLiteLLMUpstream,
  getAccountPoolCodexReview,
  getAccountPoolUpstreamSync,
  getLiteLLMCodexReview,
  getLiteLLMUpstreamSync,
  promoteLiteLLMUpstream,
  promoteAccountPoolUpstream,
  type UpstreamSyncDispatch,
  type UpstreamSyncView,
} from "../../api/AccountPoolManagementApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";

const upstreamSyncReportSnapshotFields = {
  schema_version: z.literal(1),
  state: z.enum(["idle", "queued", "running", "conflict", "failed", "passed", "promoted", "pr_created"]),
  action: z.enum(["none", "analyze", "promote"]),
  base_sha: z.string().nullable().optional(),
  candidate_sha: z.string().nullable().optional(),
  conflict_files: z.array(z.string()),
  failed_steps: z.array(z.string()),
  message: z.string(),
  pull_request_url: z.string().nullable().optional(),
  request_id: z.string().nullable().optional(),
  session_id: z.string().nullable().optional(),
  target_sha: z.string().nullable().optional(),
  target_tag: z.string().nullable().optional(),
  updated_at: z.string().nullable().optional(),
  workflow_url: z.string().nullable().optional(),
};
const upstreamSyncReportSnapshotSchema = z.object(upstreamSyncReportSnapshotFields);
const upstreamSyncStatusSnapshotFields = {
  target: z.enum(["cliproxyapi", "litellm"]),
  upstream_repository: z.string(),
  fork_repository: z.string(),
  sync_branch: z.string(),
  production_branch: z.string(),
  workflow_ref: z.string(),
  current_tag: z.string(),
  latest_tag: z.string(),
  latest_release_url: z.string(),
  update_available: z.boolean(),
  dispatch_configured: z.boolean(),
  configuration_issues: z.array(z.string()),
  report_stale: z.boolean(),
  report: upstreamSyncReportSnapshotSchema,
};
const upstreamSyncStatusSnapshotSchema = z.object(upstreamSyncStatusSnapshotFields) satisfies z.ZodType<UpstreamSyncView>;

const cachedUpstreamSyncStatusSchema = z.object({ status: upstreamSyncStatusSnapshotSchema });

const getUpstreamSyncStatusCacheKey = (target: "cliproxyapi" | "litellm") =>
  `${UPSTREAM_SYNC_STATUS_CACHE_PREFIX}${encodeURIComponent(getRequestBaseUrl())}:${target}`;

const readUpstreamSyncStatusSnapshot = (
  key: string,
  target: "cliproxyapi" | "litellm",
): UpstreamSyncView | undefined => {
  if (typeof window === "undefined") return undefined;
  try {
    const raw = window.sessionStorage.getItem(key);
    if (raw === null) return undefined;
    const parsed = cachedUpstreamSyncStatusSchema.safeParse(JSON.parse(raw));
    if (!parsed.success || parsed.data.status.target !== target) {
      window.sessionStorage.removeItem(key);
      return undefined;
    }
    return parsed.data.status;
  } catch {
    return undefined;
  }
};

interface AccountPoolUpstreamSyncPanelProps {
  accessToken: string;
  target?: "cliproxyapi" | "litellm";
}

const isPendingRequest = (status: UpstreamSyncView | undefined, requestId: string | null) =>
  requestId !== null && (status?.report.request_id !== requestId || ["queued", "running"].includes(status?.report.state ?? ""));

const stateBadgeVariant = (state: UpstreamSyncView["report"]["state"] | "queued") => {
  if (state === "passed" || state === "promoted" || state === "pr_created") return "secondary" as const;
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

export function AccountPoolUpstreamSyncPanel({ accessToken, target = "cliproxyapi" }: AccountPoolUpstreamSyncPanelProps) {
  const { t } = useTranslation();
  const isLiteLLM = target === "litellm";
  const targetLabel = isLiteLLM ? "LiteLLM" : "CLIProxyAPI";
  const [pendingRequestId, setPendingRequestId] = useState<string | null>(null);
  const [promotionOpen, setPromotionOpen] = useState(false);
  const forceRefresh = useRef(false);
  const queryClient = useQueryClient();
  const queryKey = useMemo(() => accountPoolQueryKeys.upstreamSync(accessToken, target), [accessToken, target]);
  const statusSnapshotKey = getUpstreamSyncStatusCacheKey(target);
  useLayoutEffect(() => {
    const snapshot = readUpstreamSyncStatusSnapshot(statusSnapshotKey, target);
    if (snapshot !== undefined && queryClient.getQueryData(queryKey) === undefined) {
      queryClient.setQueryData(queryKey, snapshot, { updatedAt: 0 });
    }
  }, [queryClient, queryKey, statusSnapshotKey, target]);
  const statusQueryOptions: UseQueryOptions<UpstreamSyncView> = {
    queryKey,
    queryFn: () =>
      isLiteLLM
        ? getLiteLLMUpstreamSync(accessToken, null, forceRefresh.current)
        : getAccountPoolUpstreamSync(accessToken, null, forceRefresh.current),
    retry: false,
    staleTime: 30_000,
  };
  const statusQuery = useQuery(statusQueryOptions);
  useEffect(() => {
    if (typeof window === "undefined" || !statusQuery.isSuccess || !statusQuery.isFetched) return;
    const status = statusQuery.data;
    if (status === undefined) return;
    try {
      window.sessionStorage.setItem(statusSnapshotKey, JSON.stringify({ status }));
    } catch {
      return;
    }
  }, [statusQuery.data, statusQuery.dataUpdatedAt, statusQuery.isFetched, statusQuery.isSuccess, statusSnapshotKey]);
  const taskQuery = useQuery({
    queryKey: [...queryKey, "task", pendingRequestId],
    queryFn: () =>
      isLiteLLM
        ? getLiteLLMUpstreamSync(accessToken, pendingRequestId)
        : getAccountPoolUpstreamSync(accessToken, pendingRequestId),
    enabled: pendingRequestId !== null,
    retry: false,
    refetchInterval: (query) => (isPendingRequest(query.state.data, pendingRequestId) ? 10_000 : false),
    refetchOnReconnect: false,
    refetchOnWindowFocus: false,
  });
  const waitingForReport = isPendingRequest(taskQuery.data, pendingRequestId);
  const taskFinished =
    pendingRequestId !== null &&
    taskQuery.data?.report.request_id === pendingRequestId &&
    !["queued", "running"].includes(taskQuery.data.report.state);
  const status =
    taskFinished && statusQuery.dataUpdatedAt > taskQuery.dataUpdatedAt
      ? statusQuery.data ?? taskQuery.data
      : taskQuery.data ?? statusQuery.data;

  const refreshStatus = () => {
    forceRefresh.current = true;
    void statusQuery.refetch().finally(() => {
      forceRefresh.current = false;
    });
  };

  const startTracking = (dispatch: UpstreamSyncDispatch) => {
    setPendingRequestId(dispatch.request_id);
  };
  useEffect(() => {
    if (!taskFinished) return;
    void queryClient.invalidateQueries({ queryKey, exact: true });
  }, [taskFinished, queryClient, queryKey]);
  const analyzeMutation = useMutation({
    mutationFn: () => (isLiteLLM ? analyzeLiteLLMUpstream(accessToken) : analyzeAccountPoolUpstream(accessToken)),
    onSuccess: (dispatch) => {
      startTracking(dispatch);
      toast.success(t("accountPool.upstreamSync.analysisQueued"));
    },
    onError: (error: Error) => toast.fromError(error),
  });
  const promoteMutation = useMutation({
    mutationFn: () => (isLiteLLM ? promoteLiteLLMUpstream(accessToken) : promoteAccountPoolUpstream(accessToken)),
    onSuccess: (dispatch) => {
      setPromotionOpen(false);
      startTracking(dispatch);
      toast.success(t(`accountPool.upstreamSync.${target}.promotionQueued`));
    },
    onError: (error: Error) => toast.fromError(error),
  });
  const reviewMutation = useMutation({
    mutationFn: () => (isLiteLLM ? getLiteLLMCodexReview(accessToken) : getAccountPoolCodexReview(accessToken)),
    onSuccess: (review) => {
      downloadReview(review.filename, review.content);
      toast.success(t("accountPool.upstreamSync.reviewDownloaded"));
    },
    onError: (error: Error) => toast.fromError(error),
  });

  if (!status && (statusQuery.isPending || (pendingRequestId !== null && taskQuery.isPending))) {
    return <Skeleton className="h-72 w-full" />;
  }
  if (!status) {
    const statusError = statusQuery.error ?? taskQuery.error;
    const rateLimited = statusError instanceof ApiError && statusError.status === 429;
    return (
      <Card>
        <CardContent className="flex items-center justify-between gap-4 p-6" role="alert">
          <p className="text-sm text-destructive">
            {t(rateLimited ? "accountPool.upstreamSync.rateLimited" : "accountPool.upstreamSync.loadFailed", {
              target: targetLabel,
            })}
          </p>
          <Button type="button" variant="outline" onClick={refreshStatus}>
            {t("accountPool.retry")}
          </Button>
        </CardContent>
      </Card>
    );
  }

  const report = status.report;
  const displayedState = waitingForReport && report.request_id !== pendingRequestId ? "queued" : report.state;
  const busy = analyzeMutation.isPending || promoteMutation.isPending || waitingForReport;
  const promotable = !status.report_stale && report.state === "passed" && report.target_tag === status.latest_tag;

  return (
    <div className="grid gap-5" data-testid="account-pool-upstream-sync">
      {waitingForReport && (
        <div className="flex items-center gap-3 text-sm">
          <a href={`https://github.com/${status.fork_repository}/actions`} target="_blank" rel="noreferrer">
            {t("accountPool.upstreamSync.openActions")}
          </a>
          <Button variant="outline" onClick={() => setPendingRequestId(null)}>
            {t("accountPool.upstreamSync.stopTracking")}
          </Button>
        </div>
      )}
      <div>
        <h2 className="text-lg font-semibold">{t("accountPool.upstreamSync.title", { target: targetLabel })}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{t(`accountPool.upstreamSync.${target}.description`)}</p>
      </div>
      <VersionStatusCard
        status={status}
        displayedState={displayedState}
        busy={busy}
        fetching={statusQuery.isFetching || taskQuery.isFetching}
        reviewPending={reviewMutation.isPending}
        promotable={promotable}
        onRefresh={refreshStatus}
        onAnalyze={() => analyzeMutation.mutate()}
        onReview={() => reviewMutation.mutate()}
        onPromote={() => setPromotionOpen(true)}
      />
      <CompatibilityReportCard status={status} displayedState={displayedState} waitingForReport={waitingForReport} />
      <PromotionDialog
        target={target}
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
            {t(`accountPool.upstreamSync.${status.target}.promote`)}
          </Button>
        </div>
        {!status.dispatch_configured && (
          <p className="text-sm text-amber-700 dark:text-amber-400" role="status">
            {t("accountPool.upstreamSync.authorizationMissing")}
          </p>
        )}
        <p className="text-xs text-muted-foreground">{t("accountPool.upstreamSync.baselineNote")}</p>
        <div className="grid gap-2 text-xs sm:grid-cols-2">
          <span>{t("accountPool.upstreamSync.sourceRepository")}: {status.upstream_repository}</span>
          <span>{t("accountPool.upstreamSync.forkRepository")}: {status.fork_repository}</span>
          <span>{t("accountPool.upstreamSync.productionBranch")}: {status.production_branch}</span>
          <span>{t("accountPool.upstreamSync.workflowRef")}: {status.workflow_ref}</span>
        </div>
        {(status.configuration_issues ?? []).map((issue) => (
          <p key={issue} role="status" className="text-sm text-amber-700 dark:text-amber-400">
            {t(`accountPool.upstreamSync.issues.${issue}`, { defaultValue: t("accountPool.upstreamSync.issues.configuration_unavailable") })}
          </p>
        ))}
        {status.report_stale && <p role="status">{t("accountPool.upstreamSync.staleReport")}</p>}
        <p className="text-xs text-muted-foreground">{t(`accountPool.upstreamSync.${status.target}.safetyNote`)}</p>
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
    : t(`accountPool.upstreamSync.${status.target}.stateDescriptions.${report.state}`);
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
          {report.pull_request_url && <ExternalLinkItem href={report.pull_request_url} label={t("accountPool.upstreamSync.openPullRequest")} />}
          {report.workflow_url && (
            <ExternalLinkItem href={report.workflow_url} label={t("accountPool.upstreamSync.openWorkflow")} />
          )}
        </div>
      </CardContent>
    </Card>
  );
}

interface PromotionDialogProps {
  target: "litellm" | "cliproxyapi";
  open: boolean;
  targetTag: string;
  pending: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}

function PromotionDialog({ target, open, targetTag, pending, onOpenChange, onConfirm }: PromotionDialogProps) {
  const { t } = useTranslation();
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t(`accountPool.upstreamSync.${target}.confirmPromotionTitle`)}</AlertDialogTitle>
          <AlertDialogDescription>
            {t(`accountPool.upstreamSync.${target}.confirmPromotionDescription`, { tag: targetTag })}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>{t("accountPool.cancel")}</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={pending} onClick={onConfirm}>
            {t(`accountPool.upstreamSync.${target}.confirmPromotion`)}
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
