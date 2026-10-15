/** 本文件区分本机镜像与备份归档，只提交用户勾选的版本，不自动备份或清理。 */
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
      <CardTitle>本机镜像与备份管理</CardTitle>
      <CardDescription>扫描只列清单，不拉取、不构建、不自动备份。删除本机镜像保留归档，删除备份不清理 Docker 镜像。</CardDescription>
    </CardHeader>
    <CardContent className="space-y-5">
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" disabled={busy || query.isFetching} onClick={() => { setImages([]); void query.refetch(); }}>扫描本机镜像</Button>
        <Button disabled={busy || query.isFetching || stale || !selected.length} onClick={() => prepare(
          { action: "backup_selected", version_ids: selected.map((item) => item.pair.id) },
          `仅备份所选版本：${selected.map((item) => item.pair.commit.slice(0, 10)).join("、")}。${data.database_backups_enabled && selected.some((item) => item.current) ? "包含当前运行版本，将暂停业务并刷新两库快照。" : "历史镜像只归档，不生成历史数据库快照。"}`,
        )}>备份所选镜像</Button>
        <Button variant="destructive" disabled={busy || query.isFetching || stale || !canDeleteImages} onClick={() => prepare(
          { action: "delete_images", version_ids: selected.map((item) => item.pair.id) },
          `删除所选本机 Docker 镜像：${selected.map((item) => item.pair.commit.slice(0, 10)).join("、")}。保留镜像归档；执行前重新校验归档和所有容器引用，不删除数据卷。`,
        )}>删除所选本机镜像</Button>
      </div>
      {query.isError && <p role="alert" className="text-destructive">{query.error.message}</p>}
      {query.data && <div className="max-h-72 space-y-2 overflow-auto rounded-lg border p-3">
        {stale && <p role="status">备份或任务已变化，请重新扫描。</p>}
        {!local.length && <p>未发现本项目的配套镜像。</p>}
        {local.map((item) => <div key={item.pair.id} className="flex items-start gap-3 text-sm">
          <Checkbox aria-label={`选择本机镜像 ${item.pair.commit.slice(0, 10)}`} checked={images.includes(item.pair.id)} disabled={busy || stale} onCheckedChange={(checked) => setImages(toggle(images, item.pair.id, checked === true))} />
          <span className="min-w-0 break-all"><code>{item.pair.commit.slice(0, 10)}</code> · {item.current ? "当前运行 · " : ""}{item.backed_up ? "已有备份" : "未备份，删除前请先备份"}
            {item.image_users.length > 0 && <span className="block text-muted-foreground">受保护：容器引用 {item.image_users.join("、")}</span>}
            {item.protected_reason && <span className="block text-muted-foreground">受保护：{item.protected_reason}</span>}
          </span>
        </div>)}
      </div>}
      <details className="rounded-lg border p-3" open>
        <summary className="cursor-pointer font-medium">批量管理备份归档</summary>
        <div className="my-3 max-h-64 space-y-2 overflow-auto">
          {data.versions.filter((item) => item.backup).map((item) => <div key={item.pair.id} className="flex items-start gap-3 text-sm">
            <Checkbox aria-label={`选择备份 ${item.pair.commit.slice(0, 10)}`} disabled={busy || item.current} checked={backups.includes(item.pair.id)} onCheckedChange={(checked) => setBackups(toggle(backups, item.pair.id, checked === true))} />
            <span><code>{item.pair.commit.slice(0, 10)}</code>{item.current ? " · 当前运行，不可删除" : ""}{item.note ? ` · ${item.note}` : ""}</span>
          </div>)}
        </div>
        <Button variant="destructive" disabled={busy || !selectedBackups.length} onClick={() => prepare(
          { action: "delete_backups", version_ids: selectedBackups.map((item) => item.pair.id) },
          `删除所选备份：${selectedBackups.map((item) => item.pair.commit.slice(0, 10)).join("、")}。将永久删除归档、快照和备注，不删除 Docker 镜像；扫描不会自动重新备份。`,
        )}>删除所选备份</Button>
      </details>
    </CardContent>
  </Card>;
}
