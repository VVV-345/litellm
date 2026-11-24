/** 本文件展示项目运行版本、真实镜像备份及实际回退操作。 */
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient, type Query } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { toast } from "@/lib/toast";
import { ReleaseVersionCard, ReleaseStorageCard, ReleaseCommandsCard } from "./AccountPoolReleaseCards";
import { AccountPoolReleaseConfirmation } from "./AccountPoolReleaseConfirmation";
import { AccountPoolImageManager } from "./AccountPoolImageManager";
import { AccountPoolRuntimeCards } from "./AccountPoolRuntimeCards";
import {
  executeRelease,
  listReleases,
  prepareRelease,
  releaseCommands,
  type ReleaseAction,
  type ReleaseConfirmation,
  type ReleaseView,
  type ReleaseJob,
} from "../../api/AccountPoolReleasesApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";

const actionTitles: Record<ReleaseAction["action"], string> = {
  apply: "accountPool.releaseManagement.reviewAndConfirmRollback",
  delete: "accountPool.releaseManagement.deleteImageBackup",
  note: "accountPool.releaseManagement.saveVersionNotes",
  guide: "accountPool.releaseManagement.saveRollbackExplanation",
  scan: "accountPool.releaseManagement.backUpTheRunningVersion",
  backup_selected: "accountPool.imageArchive.backUpSelectedImages",
  delete_backups: "accountPool.releaseManagement.deleteBackupsInBulk",
  delete_images: "accountPool.releaseManagement.deleteLocalImagesInBulk",
  deploy: "accountPool.releaseManagement.deployNewVersion",
  recover: "accountPool.releaseManagement.restoreRunningVersion",
  restore_data: "accountPool.releaseManagement.restoreApplicationAndDatabaseSnapshots",
  upgrade_cards: "accountPool.releaseManagement.confirmUpgradeOfSelectedCards",
};
const jobLabels = {
  queued: "accountPool.releaseManagement.queued",
  running: "accountPool.upstreamSync.states.running",
  succeeded: "accountPool.managerLogs.states.completed",
  failed: "accountPool.releaseManagement.failed",
  recovered: "accountPool.releaseManagement.switchFailedOriginalVersionRestored",
  interrupted: "accountPool.releaseManagement.taskInterrupted",
};

const jobIsPending = (job: ReleaseJob | null | undefined) => job?.status === "queued" || job?.status === "running";
const selectedVersion = (data: ReleaseView | undefined, id: string | null) =>
  data?.versions.find((version) => version.pair.id === id) ??
  data?.versions.find((version) => version.current) ??
  data?.versions[0];

export function AccountPoolReleasesPanel({ accessToken }: { accessToken: string }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const queryKey = accountPoolQueryKeys.releases(accessToken);
  const queryOptions = {
    queryKey,
    queryFn: () => listReleases(accessToken),
    refetchInterval: (state: Query<ReleaseView>) => (jobIsPending(state.state.data?.job) ? 2000 : 15000),
    retry: false,
  };
  const query = useQuery(queryOptions);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [pending, setPending] = useState<{
    confirmation: ReleaseConfirmation;
    description: string;
    before?: string;
  } | null>(null);
  const data = query.data;
  const selected = selectedVersion(data, selectedId);
  const commandOptions = {
    queryKey: accountPoolQueryKeys.releaseCommands(accessToken, selected?.pair.id),
    queryFn: () => releaseCommands(accessToken, selected!.pair.id),
    enabled: !!selected,
    retry: false,
  };
  const commands = useQuery(commandOptions);
  const refresh = () =>
    Promise.all([
      client.invalidateQueries({ queryKey }),
      client.invalidateQueries({ queryKey: accountPoolQueryKeys.releaseImages(accessToken) }),
      client.invalidateQueries({ queryKey: accountPoolQueryKeys.releaseCommandsRoot(accessToken) }),
    ]);
  const preparation = useMutation({
    mutationFn: ({ action }: { action: ReleaseAction; description: string; before?: string }) =>
      prepareRelease(accessToken, action),
    onSuccess: (confirmation, context) =>
      setPending({ confirmation, description: context.description, before: context.before }),
    onError: (error) => {
      setPending(null);
      toast.fromError(error);
      void refresh();
    },
  });
  const execution = useMutation({
    mutationFn: ({ token, acknowledgement }: { token: string; acknowledgement?: string }) =>
      acknowledgement ? executeRelease(accessToken, token, acknowledgement) : executeRelease(accessToken, token),
    onSuccess: (job) => {
      setPending(null);
      setNote(null);
      client.setQueryData(queryKey, data ? { ...data, job } : data);
      toast.success(t("accountPool.releaseManagement.taskSubmittedResultsWillAppearHere"));
      void refresh();
    },
    onError: (error) => {
      toast.fromError(error);
      void refresh();
    },
  });
  const mutationPending = preparation.isPending || execution.isPending;
  const jobPending = jobIsPending(data?.job);
  const busy = mutationPending || !!pending || jobPending;
  const prepare = (action: Omit<ReleaseAction, "revision">, description: string, before?: string) => {
    if (!data) return;
    preparation.mutate({ action: { ...action, revision: data.revision }, description, before });
  };
  const selectRollback = (id: string) => {
    setSelectedId(id);
    setNote(null);
    prepare({ action: "apply", version_id: id }, t("accountPool.releaseManagement.recheckCompatibilityForTheSelectedVersion"));
  };
  if (!data)
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t("accountPool.releaseManagement.versionManagement")}</CardTitle>
          <CardDescription>
            {query.isError ? t("accountPool.releaseManagement.versionManagementIsUnavailableCheckWhether") : t("accountPool.releaseManagement.loadingRunningVersionAndBackups")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {query.isError && (
            <p role="alert" className="mb-3 text-sm text-destructive">
              {query.error.message}
            </p>
          )}
          <Button variant="outline" onClick={() => void query.refetch()} disabled={query.isFetching}>
            {t("accountPool.releaseManagement.reload")}</Button>
        </CardContent>
      </Card>
    );
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">{t("accountPool.releaseManagement.versionManagement")}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.releaseManagement.managePairedImageBackupsForLitellm")}</p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            disabled={busy || !data.current}
            onClick={() =>
              prepare(
                { action: "scan" },
                data.database_backups_enabled
                  ? t("accountPool.releaseManagement.refreshOnlyTheRunningVersionS")
                  : t("accountPool.releaseManagement.archiveOnlyTheRunningVersionHistorical"),
              )
            }
          >
            {t("accountPool.releaseManagement.backUpCurrentVersion")}</Button>
          <Button variant="ghost" aria-label={t("accountPool.releaseManagement.refreshVersionList")} disabled={query.isFetching} onClick={() => void refresh()}>
            <RefreshCw className={query.isFetching ? "animate-spin" : ""} />
          </Button>
        </div>
      </div>
      <AccountPoolImageManager accessToken={accessToken} data={data} busy={busy} prepare={prepare} />
      <AccountPoolRuntimeCards accessToken={accessToken} busy={busy} prepare={prepare} />
      {query.isError && (
        <p role="alert" className="rounded-lg border border-destructive/30 p-3 text-sm text-destructive">
          {t("accountPool.releaseManagement.temporarilyDisconnectedShowingTheLastData")}</p>
      )}
      {data.problems?.map((problem) => (
        <p key={problem} role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      ))}
      {data.job && (
        <div role="status" className="rounded-xl border bg-muted/30 px-4 py-3 text-sm">
          <p className="font-medium">
            {t(jobLabels[data.job.status])} · {data.job.phase}
          </p>
          {data.job.message && <p className="mt-1 text-muted-foreground">{data.job.message}</p>}
        </div>
      )}
      {preparation.isPending && preparation.variables.action.action === "apply" && (
        <p role="status" className="flex items-center gap-2 rounded-xl border bg-muted/30 p-4 text-sm">
          <RefreshCw className="size-4 shrink-0 animate-spin" />
          {t("accountPool.releaseManagement.checkingImageConfigurationAndFeatureDifferences")}</p>
      )}
      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        {selected ? (
          <ReleaseVersionCard
            data={data}
            selected={selected}
            busy={busy}
            note={note}
            setNote={setNote}
            setSelectedId={setSelectedId}
            prepare={prepare}
          />
        ) : (
          <Card>
            <CardHeader>
              <CardTitle>{t("accountPool.releaseManagement.noVersionsAvailable")}</CardTitle>
              <CardDescription>{t("accountPool.releaseManagement.checkTheRunningVersionOrScan")}</CardDescription>
            </CardHeader>
          </Card>
        )}
        <ReleaseStorageCard data={data} />
      </div>
      {selected && <ReleaseCommandsCard selected={selected} commands={commands.data} error={commands.isError} />}
      {pending && (
        <AccountPoolReleaseConfirmation
          key={pending.confirmation.token}
          {...pending}
          title={t(actionTitles[pending.confirmation.action.action])}
          busy={execution.isPending || preparation.isPending}
          stale={
            pending.confirmation.action.revision !== data.revision ||
            pending.confirmation.current_commit !== data.current?.commit ||
            jobPending
          }
          onSelectVersion={selectRollback}
          onForce={() =>
            prepare(
              {
                action: "apply",
                version_id: pending.confirmation.action.version_id,
                force: true,
                force_acknowledgement: pending.confirmation.rollback?.target_commit,
              },
              t("accountPool.releaseManagement.forcedRollbackSelectedReviewTheUnverified"),
            )
          }
          onClose={() => setPending(null)}
          onConfirm={(acknowledgement) => execution.mutate({ token: pending.confirmation.token, acknowledgement })}
        />
      )}
    </div>
  );
}
