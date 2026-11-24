/** 本文件区分本机镜像与备份归档，只提交用户勾选的版本，不自动备份或清理。 */
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { scanReleaseImages, type ReleaseAction, type ReleaseView } from "../../api/AccountPoolReleasesApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";

export function AccountPoolImageManager({ accessToken, data, busy, prepare }: {
  accessToken: string;
  data: ReleaseView;
  busy: boolean;
  prepare: (action: Omit<ReleaseAction, "revision">, description: string) => void;
}) {
  const { t } = useTranslation();
  const [images, setImages] = useState<string[]>([]);
  const [backups, setBackups] = useState<string[]>([]);
  const query = useQuery({
    queryKey: accountPoolQueryKeys.releaseImages(accessToken),
    queryFn: () => scanReleaseImages(accessToken),
    enabled: false,
    retry: false,
    staleTime: Infinity,
  });
  const local = query.data?.versions ?? [];
  const selected = local.filter((item) => images.includes(item.pair.id));
  const selectedBackups = data.versions.filter((item) => backups.includes(item.pair.id) && item.backup && !item.current);
  const stale = query.data?.revision !== data.revision || query.isStale;
  const canDeleteImages = selected.length > 0 && selected.every((item) =>
    !item.current && item.backed_up && !item.image_users.length && !item.protected_reason);
  const toggle = (values: string[], id: string, checked: boolean) => checked ? [...values, id] : values.filter((value) => value !== id);
  return <Card>
    <CardHeader>
      <CardTitle>{t("accountPool.imageArchive.localImagesAndBackups")}</CardTitle>
      <CardDescription>{t("accountPool.imageArchive.scanningOnlyListsImagesItDoes")}</CardDescription>
    </CardHeader>
    <CardContent className="space-y-5">
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" disabled={busy || query.isFetching} onClick={() => { setImages([]); void query.refetch(); }}>{t("accountPool.imageArchive.scanLocalImages")}</Button>
        <Button disabled={busy || query.isFetching || stale || !selected.length} onClick={() => prepare(
          { action: "backup_selected", version_ids: selected.map((item) => item.pair.id) },
          t("accountPool.imageArchive.backUpOnlyTheSelectedVersions", { versions: selected.map((item) => item.pair.commit.slice(0, 10)).join(t("accountPool.credentialFields.listSeparator")), impact: data.database_backups_enabled && selected.some((item) => item.current) ? t("accountPool.imageArchive.includesTheRunningVersionServiceWill") : t("accountPool.imageArchive.historicalImagesAreArchivedWithoutCreating") }),
        )}>{t("accountPool.imageArchive.backUpSelectedImages")}</Button>
        <Button variant="destructive" disabled={busy || query.isFetching || stale || !canDeleteImages} onClick={() => prepare(
          { action: "delete_images", version_ids: selected.map((item) => item.pair.id) },
          t("accountPool.imageArchive.removeTheSelectedLocalDockerImages", { versions: selected.map((item) => item.pair.commit.slice(0, 10)).join(t("accountPool.credentialFields.listSeparator")) }),
        )}>{t("accountPool.imageArchive.removeSelectedLocalImages")}</Button>
      </div>
      {query.isError && <p role="alert" className="text-destructive">{query.error.message}</p>}
      {query.data && <div className="max-h-72 space-y-2 overflow-auto rounded-lg border p-3">
        {stale && <p role="status">{t("accountPool.imageArchive.backupsOrTasksHaveChangedScan")}</p>}
        {!local.length && <p>{t("accountPool.imageArchive.noMatchingProjectImagesFound")}</p>}
        {local.map((item) => <div key={item.pair.id} className="flex items-start gap-3 text-sm">
          <Checkbox aria-label={t("accountPool.imageArchive.selectLocalImage", { version: item.pair.commit.slice(0, 10) })} checked={images.includes(item.pair.id)} disabled={busy || stale} onCheckedChange={(checked) => setImages(toggle(images, item.pair.id, checked === true))} />
          <span className="min-w-0 break-all"><code>{item.pair.commit.slice(0, 10)}</code> · {item.current ? t("accountPool.imageArchive.currentlyRunning") : ""}{item.backed_up ? t("accountPool.imageArchive.backedUp") : t("accountPool.imageArchive.notBackedUpBackUpBefore")}
            {item.image_users.length > 0 && <span className="block text-muted-foreground">{t("accountPool.imageArchive.protectedReferencedByAContainer")} {item.image_users.join(t("accountPool.credentialFields.listSeparator"))}</span>}
            {item.protected_reason && <span className="block text-muted-foreground">{t("accountPool.imageArchive.protected")}{item.protected_reason}</span>}
          </span>
        </div>)}
      </div>}
      <details className="rounded-lg border p-3" open>
        <summary className="cursor-pointer font-medium">{t("accountPool.imageArchive.manageBackupArchives")}</summary>
        <div className="my-3 max-h-64 space-y-2 overflow-auto">
          {data.versions.filter((item) => item.backup).map((item) => <div key={item.pair.id} className="flex items-start gap-3 text-sm">
            <Checkbox aria-label={t("accountPool.imageArchive.selectBackup", { version: item.pair.commit.slice(0, 10) })} disabled={busy || item.current} checked={backups.includes(item.pair.id)} onCheckedChange={(checked) => setBackups(toggle(backups, item.pair.id, checked === true))} />
            <span><code>{item.pair.commit.slice(0, 10)}</code>{item.current ? t("accountPool.imageArchive.runningCannotBeDeleted") : ""}{item.note ? ` · ${item.note}` : ""}</span>
          </div>)}
        </div>
        <Button variant="destructive" disabled={busy || !selectedBackups.length} onClick={() => prepare(
          { action: "delete_backups", version_ids: selectedBackups.map((item) => item.pair.id) },
          t("accountPool.imageArchive.deleteTheSelectedBackupsThisPermanently", { versions: selectedBackups.map((item) => item.pair.commit.slice(0, 10)).join(t("accountPool.credentialFields.listSeparator")) }),
        )}>{t("accountPool.imageArchive.deleteSelectedBackups")}</Button>
      </details>
    </CardContent>
  </Card>;
}
