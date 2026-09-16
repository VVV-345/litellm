/** 本文件渲染版本详情、存储状态与可编辑回退说明，不发起部署请求。 */
import { Archive, Copy, GitBranch, HardDrive } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/lib/toast";
import type { ReleaseAction, ReleaseVersion, ReleaseView, ReleaseCommands } from "./AccountPoolReleasesApi";
export type ReleasePrepare = (action: Omit<ReleaseAction, "revision">, description: string, before?: string) => void;
const bytes = (value: number) =>
  value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(2)} GB` : `${(value / 1024 ** 2).toFixed(1)} MB`;
const versionLabel = (version: ReleaseVersion) =>
  `${version.pair.commit.slice(0, 10)}${version.current ? " · 当前运行" : ""}${version.note ? ` · ${version.note}` : ""}`;
async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success("已复制");
  } catch {
    toast.error("复制失败，请手动选择文字复制");
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
  const canChange = !busy && !!data.current && !selected.current;
  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Archive className="size-4" />
          选择版本
        </CardTitle>
        <CardDescription>从服务器保存的镜像归档恢复，应用前自动备份当前版本</CardDescription>
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
            <SelectTrigger aria-label="选择备份版本" className="min-w-0 flex-1 basis-64">
              <SelectValue>{versionLabel(selected)}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {data.versions.map((version) => (
                <SelectItem key={version.pair.id} value={version.pair.id}>
                  {versionLabel(version)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            disabled={!canChange || !selected.available}
            onClick={() =>
              prepare(
                { action: "apply", version_id: selected.pair.id },
                `从当前 ${data.current?.commit.slice(0, 10)} 切换到 ${versionLabel(selected)}。会短暂中断请求；健康检查失败会尝试恢复原版本。`,
              )
            }
          >
            应用
          </Button>
          <Button
            variant="destructive"
            disabled={!canChange || !selected.backup}
            onClick={() =>
              prepare(
                { action: "delete", version_id: selected.pair.id },
                `删除 ${versionLabel(selected)} 的备份文件及备注，释放 ${bytes(selected.backup?.archive_bytes ?? 0)}。删除后不能从此备份恢复。`,
              )
            }
          >
            删除
          </Button>
        </div>
        <>
          <div className="flex flex-wrap gap-2">
            <Badge variant={selected.current ? "default" : "secondary"}>
              {selected.current ? "当前运行" : "历史版本"}
            </Badge>
            <Badge variant="outline">{selected.available ? "已有镜像备份" : "无完整备份"}</Badge>
          </div>
          <div className="rounded-lg border bg-muted/20 p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-muted-foreground">Git commit</span>
              <Button
                size="icon-xs"
                variant="ghost"
                aria-label="复制完整 commit"
                onClick={() => void copy(selected.pair.commit)}
              >
                <Copy />
              </Button>
            </div>
            <code className="mt-1 block break-all text-sm">{selected.pair.commit}</code>
          </div>
          {selected.backup && (
            <dl className="grid gap-3 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-muted-foreground">备份时间</dt>
                <dd className="mt-1">{new Date(selected.backup.created_at * 1000).toLocaleString()}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">归档大小</dt>
                <dd className="mt-1">{bytes(selected.backup.archive_bytes)}</dd>
              </div>
              <div className="sm:col-span-2">
                <dt className="text-muted-foreground">部署配置来源</dt>
                <dd className="mt-1">
                  {selected.backup.configuration_source === "running"
                    ? "备份时的运行配置"
                    : "历史镜像导入时的当前配置，请先核对环境兼容性"}
                </dd>
              </div>
            </dl>
          )}
          {selected.problem && <p className="text-sm text-destructive">{selected.problem}</p>}
          <div className="border-t pt-4">
            <div className="mb-2 flex items-center justify-between gap-2">
              <label htmlFor="release-note" className="text-sm font-medium">
                版本备注
              </label>
              {note === null && (
                <Button variant="ghost" size="sm" disabled={busy} onClick={() => setNote(selected.note ?? "")}>
                  编辑备注
                </Button>
              )}
            </div>
            {note === null ? (
              <p className="whitespace-pre-wrap break-words text-sm text-muted-foreground">
                {selected.note || "暂无备注"}
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
                        `修改 ${selected.pair.commit.slice(0, 10)} 的备注，等待 5 秒后确认保存。`,
                        selected.note,
                      )
                    }
                  >
                    保存备注
                  </Button>
                  <Button variant="ghost" size="sm" disabled={busy} onClick={() => setNote(null)}>
                    取消编辑
                  </Button>
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
  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <HardDrive className="size-4" />
          运行与存储
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div>
          <p className="text-muted-foreground">当前运行 commit</p>
          <code className="mt-1 block break-all">{data.current?.commit ?? "未知"}</code>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <p className="text-muted-foreground">完整备份</p>
            <p className="mt-1 text-xl font-semibold">
              {data.versions.filter((version) => version.available).length}
              <span className="ml-1 text-sm font-normal">份</span>
            </p>
          </div>
          <div>
            <p className="text-muted-foreground">磁盘可用</p>
            <p className="mt-1 text-xl font-semibold">{bytes(data.free_bytes)}</p>
          </div>
        </div>
        <div>
          <p className="text-muted-foreground">服务器备份目录</p>
          <code className="mt-1 block break-all text-xs">{data.location}</code>
        </div>
        <p className="border-t pt-3 leading-6 text-muted-foreground">
          备份包含两份镜像与部署配置。数据库、认证文件和日志独立保存，镜像回退不会回退这些数据。删除只清理选中的备份文件。
        </p>
      </CardContent>
    </Card>
  );
}
export function ReleaseGuideCard({
  data,
  guide,
  setGuide,
  busy,
  prepare,
}: {
  data: ReleaseView;
  guide: string | null;
  setGuide: (value: string | null) => void;
  busy: boolean;
  prepare: ReleasePrepare;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-wrap flex-row items-start justify-between gap-3">
        <div>
          <CardTitle>代码回退说明</CardTitle>
          <CardDescription className="mt-2">可按自己的操作习惯修改，保存前有 5 秒确认</CardDescription>
        </div>
        {guide === null && (
          <Button variant="outline" disabled={busy} onClick={() => setGuide(data.guide)}>
            编辑说明
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {guide === null ? (
          <p className="whitespace-pre-wrap break-words text-sm leading-7 text-muted-foreground">{data.guide}</p>
        ) : (
          <div className="space-y-3">
            <Textarea
              aria-label="代码回退说明"
              className="min-h-64"
              maxLength={12000}
              value={guide}
              onChange={(event) => setGuide(event.target.value)}
            />
            <div className="flex flex-wrap gap-2">
              <Button
                disabled={busy || guide === data.guide}
                onClick={() =>
                  prepare(
                    { action: "guide", text: guide },
                    "请核对说明卡片的修改内容，确认后将保存到服务器。",
                    data.guide,
                  )
                }
              >
                保存说明
              </Button>
              <Button variant="outline" disabled={busy} onClick={() => setGuide(data.default_guide)}>
                填入默认说明
              </Button>
              <Button variant="ghost" disabled={busy} onClick={() => setGuide(null)}>
                取消编辑
              </Button>
            </div>
          </div>
        )}
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
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <GitBranch className="size-4" />从 {selected.pair.commit.slice(0, 10)} 的代码继续修改
        </CardTitle>
        <CardDescription>
          在电脑上的项目仓库执行 PowerShell 命令。服务器切换镜像不会自动修改本地 Git 分支。
        </CardDescription>
      </CardHeader>
      <CardContent className="grid min-w-0 gap-4 lg:grid-cols-2">
        {error && (
          <p role="alert" className="text-sm text-destructive">
            无法生成命令，请刷新版本列表后重试
          </p>
        )}
        {!error &&
          commands &&
          (
            [
              { key: "branch", title: "创建修复分支（推荐）", detail: "适合跨多个版本回退，保留原分支历史" },
              { key: "revert", title: "git revert 撤销改动", detail: "在当前分支创建撤销提交，保留已推送历史" },
            ] as const
          ).map((item) => (
            <div key={item.key} className="min-w-0 rounded-xl border p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-sm font-medium">{item.title}</h3>
                <Button variant="ghost" size="sm" onClick={() => void copy(commands![item.key])}>
                  <Copy />
                  复制命令
                </Button>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">{item.detail}</p>
              <pre className="mt-3 max-h-64 overflow-auto rounded-lg bg-muted p-3 text-xs leading-6">
                {commands[item.key]}
              </pre>
            </div>
          ))}
        {!error && !commands && <p className="text-sm text-muted-foreground">正在生成命令…</p>}
      </CardContent>
    </Card>
  );
}
