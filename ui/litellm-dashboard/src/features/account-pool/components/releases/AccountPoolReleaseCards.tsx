/** 本文件渲染版本详情、存储状态与版本备注，不发起部署请求。 */
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Archive, Copy, DatabaseBackup, GitBranch, HardDrive } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "@/lib/toast";
import type { ReleaseAction, ReleaseVersion, ReleaseView, ReleaseCommands } from "../../api/AccountPoolReleasesApi";
type ReleasePrepare = (action: Omit<ReleaseAction, "revision">, description: string, before?: string) => void;
const bytes = (value: number) =>
  value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(2)} GB` : `${(value / 1024 ** 2).toFixed(1)} MB`;
const versionLabel = (t: TFunction, version: ReleaseVersion) =>
  `${version.pair.commit.slice(0, 10)}${version.current ? t("accountPool.releaseDetails.currentlyRunning") : ""}${version.note ? ` · ${version.note}` : ""}`;
async function copy(t: TFunction, text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(t("accountPool.releaseDetails.copied"));
  } catch {
    toast.error(t("accountPool.releaseDetails.couldNotCopySelectAndCopy"));
  }
}

export function ReleaseVersionCard({
  data,
  selected,
  busy,
  note,
  setNote,
  setSelectedId,
  prepare,
}: {
  data: ReleaseView;
  selected: ReleaseVersion;
  busy: boolean;
  note: string | null;
  setNote: (value: string | null) => void;
  setSelectedId: (id: string | null) => void;
  prepare: ReleasePrepare;
}) {
  const { t, i18n } = useTranslation();
  const canChange = !busy && !!data.current && !selected.current;
  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Archive className="size-4" />
          {t("accountPool.releaseDetails.selectAVersion")}</CardTitle>
        <CardDescription>{t("accountPool.releaseDetails.restoreFromAServerSideImage")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="flex flex-wrap gap-2">
          <Select
            value={selected.pair.id}
            onValueChange={(id) => {
              setSelectedId(id);
              setNote(null);
            }}
            disabled={busy}
          >
            <SelectTrigger aria-label={t("accountPool.releaseDetails.selectABackupVersion")} className="min-w-0 flex-1 basis-64">
              <SelectValue>{versionLabel(t, selected)}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {data.versions.map((version) => (
                <SelectItem key={version.pair.id} value={version.pair.id}>
                  {versionLabel(t, version)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            disabled={!canChange || !selected.available}
            onClick={() =>
              prepare(
                { action: "apply", version_id: selected.pair.id },
                t("accountPool.releaseDetails.switchFromToRequestsWillBe", { current: data.current?.commit.slice(0, 10), target: versionLabel(t, selected) }),
              )
            }
          >
            {t("accountPool.releaseDetails.checkAndRollBack")}</Button>
          {selected.backup?.database_snapshot && (
            <Button
              variant="outline"
              disabled={!canChange || !selected.available || !data.database_backups_enabled}
              onClick={() =>
                prepare(
                  {
                    action: "restore_data",
                    version_id: selected.pair.id,
                    snapshot_id: selected.backup!.database_snapshot!.id,
                  },
                  t("accountPool.releaseDetails.restoreThisVersionAndItsDatabase"),
                )
              }
            >
              <DatabaseBackup /> {t("accountPool.releaseDetails.restoreApplicationAndData")}</Button>
          )}
          <Button
            variant="destructive"
            disabled={!canChange || !selected.backup}
            onClick={() =>
              prepare(
                { action: "delete", version_id: selected.pair.id },
                t("accountPool.releaseDetails.deleteBackupFilesAndNotesFor", { version: versionLabel(t, selected), size: bytes(selected.backup?.archive_bytes ?? 0) }),
              )
            }
          >
            {t("accountPool.releaseDetails.delete")}</Button>
        </div>
        <>
          <div className="flex flex-wrap gap-2">
            <Badge variant={selected.current ? "default" : "secondary"}>
              {selected.current ? t("accountPool.releaseDetails.currentlyRunningDetail") : t("accountPool.releaseDetails.historicalVersion")}
            </Badge>
            <Badge variant="outline">{selected.available ? t("accountPool.releaseDetails.imageBackedUp") : t("accountPool.releaseDetails.noCompleteBackup")}</Badge>
          </div>
          <div className="rounded-lg border bg-muted/20 p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-muted-foreground">Git commit</span>
              <Button
                size="icon-xs"
                variant="ghost"
                aria-label={t("accountPool.releaseDetails.copyFullCommitHash")}
                onClick={() => void copy(t, selected.pair.commit)}
              >
                <Copy />
              </Button>
            </div>
            <code className="mt-1 block break-all text-sm">{selected.pair.commit}</code>
          </div>
          {selected.backup && (
            <dl className="grid gap-3 text-sm sm:grid-cols-2">
              <div className="sm:col-span-2">
                <dt className="text-muted-foreground">{t("accountPool.releaseDetails.databaseSnapshot")}</dt>
                <dd className="mt-1 break-all">
                  {selected.backup.database_snapshot
                    ? `${new Date(selected.backup.database_snapshot.created_at * 1000).toLocaleString(i18n.language)} · ${selected.backup.database_snapshot.id}`
                    : t("accountPool.releaseDetails.noDatabaseSnapshotOnlyApplicationRollback")}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t("accountPool.releaseDetails.backupTime")}</dt>
                <dd className="mt-1">{new Date(selected.backup.created_at * 1000).toLocaleString(i18n.language)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t("accountPool.releaseDetails.archiveSize")}</dt>
                <dd className="mt-1">{bytes(selected.backup.archive_bytes)}</dd>
              </div>
              <div className="sm:col-span-2">
                <dt className="text-muted-foreground">{t("accountPool.releaseDetails.deploymentConfigurationSource")}</dt>
                <dd className="mt-1">
                  {selected.backup.configuration_source === "running"
                    ? t("accountPool.releaseDetails.configurationAtBackupTime")
                    : t("accountPool.releaseDetails.configurationAtTheTimeTheHistorical")}
                </dd>
              </div>
            </dl>
          )}
          {selected.problem && <p className="text-sm text-destructive">{selected.problem}</p>}
          <div className="border-t pt-4">
            <div className="mb-2 flex items-center justify-between gap-2">
              <label htmlFor="release-note" className="text-sm font-medium">
                {t("accountPool.releaseDetails.versionNotes")}</label>
              {note === null && (
                <Button variant="ghost" size="sm" disabled={busy} onClick={() => setNote(selected.note ?? "")}>
                  {t("accountPool.releaseDetails.editNotes")}</Button>
              )}
            </div>
            {note === null ? (
              <p className="whitespace-pre-wrap break-words text-sm text-muted-foreground">
                {selected.note || t("accountPool.releaseDetails.noNotesYet")}
              </p>
            ) : (
              <div className="space-y-2">
                <Input
                  id="release-note"
                  value={note}
                  maxLength={500}
                  onChange={(event) => setNote(event.target.value)}
                />
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    disabled={busy || note === selected.note}
                    onClick={() =>
                      prepare(
                        { action: "note", version_id: selected.pair.id, text: note },
                        t("accountPool.releaseDetails.updateNotesForWaitFiveSeconds", { version: selected.pair.commit.slice(0, 10) }),
                        selected.note,
                      )
                    }
                  >
                    {t("accountPool.releaseDetails.saveNotes")}</Button>
                  <Button variant="ghost" size="sm" disabled={busy} onClick={() => setNote(null)}>
                    {t("accountPool.releaseDetails.cancelEditing")}</Button>
                </div>
              </div>
            )}
          </div>
        </>
      </CardContent>
    </Card>
  );
}
export function ReleaseStorageCard({ data }: { data: ReleaseView }) {
  const { t } = useTranslation();
  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <HardDrive className="size-4" />
          {t("accountPool.releaseDetails.runtimeAndStorage")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div>
          <p className="text-muted-foreground">{t("accountPool.releaseDetails.runningCommit")}</p>
          <code className="mt-1 block break-all">{data.current?.commit ?? t("accountPool.credential.result.unknown")}</code>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <p className="text-muted-foreground">{t("accountPool.releaseDetails.completeBackups")}</p>
            <p className="mt-1 text-xl font-semibold">
              {data.versions.filter((version) => version.available).length}
              <span className="ml-1 text-sm font-normal">{t("accountPool.releaseDetails.backups")}</span>
            </p>
          </div>
          <div>
            <p className="text-muted-foreground">{t("accountPool.releaseDetails.freeDiskSpace")}</p>
            <p className="mt-1 text-xl font-semibold">{bytes(data.free_bytes)}</p>
          </div>
        </div>
        <div>
          <p className="text-muted-foreground">{t("accountPool.releaseDetails.serverBackupDirectory")}</p>
          <code className="mt-1 block break-all text-xs">{data.location}</code>
        </div>
        <p className="border-t pt-3 leading-6 text-muted-foreground">
          {data.database_backups_enabled
            ? t("accountPool.releaseDetails.databaseBackupsAreEnabledBackingUp")
            : t("accountPool.releaseDetails.databaseBackupsAreDisabledOnlyImages")}
          {t("accountPool.releaseDetails.aNormalRollbackKeepsCurrentData")}</p>
      </CardContent>
    </Card>
  );
}
export function ReleaseCommandsCard({
  selected,
  commands,
  error,
}: {
  selected: ReleaseVersion;
  commands: ReleaseCommands | undefined;
  error: boolean;
}) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <GitBranch className="size-4" />{t("accountPool.releaseDetails.continueFromCommit", { commit: selected.pair.commit.slice(0, 10) })}
        </CardTitle>
        <CardDescription>
          {t("accountPool.releaseDetails.runThesePowershellCommandsInYour")}</CardDescription>
      </CardHeader>
      <CardContent className="grid min-w-0 gap-4 lg:grid-cols-2">
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {t("accountPool.releaseDetails.couldNotGenerateCommandsRefreshThe")}</p>
        )}
        {!error &&
          commands &&
          (
            [
              { key: "branch", title: t("accountPool.releaseDetails.createARepairBranchRecommended"), detail: t("accountPool.releaseDetails.usefulForRollingBackSeveralVersions") },
              { key: "revert", title: t("accountPool.releaseDetails.undoChangesWithGitRevert"), detail: t("accountPool.releaseDetails.createARevertCommitOnThe") },
            ] as const
          ).map((item) => (
            <div key={item.key} className="min-w-0 rounded-xl border p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-sm font-medium">{item.title}</h3>
                <Button variant="ghost" size="sm" onClick={() => void copy(t, commands![item.key])}>
                  <Copy />
                  {t("accountPool.releaseDetails.copyCommand")}</Button>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">{item.detail}</p>
              <pre className="mt-3 max-h-64 overflow-auto rounded-lg bg-muted p-3 text-xs leading-6">
                {commands[item.key]}
              </pre>
            </div>
          ))}
        {!error && !commands && <p className="text-sm text-muted-foreground">{t("accountPool.releaseDetails.generatingCommands")}</p>}
      </CardContent>
    </Card>
  );
}
