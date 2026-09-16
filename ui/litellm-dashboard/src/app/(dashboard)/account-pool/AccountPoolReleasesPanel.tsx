/** 本文件展示项目运行版本、真实镜像备份及可编辑的代码回退说明。 */
import { useMutation, useQuery, useQueryClient, type Query } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { toast } from "@/lib/toast";
import {
  ReleaseVersionCard,
  ReleaseStorageCard,
  ReleaseGuideCard,
  ReleaseCommandsCard,
} from "./AccountPoolReleaseCards";
import { AccountPoolReleaseConfirmation } from "./AccountPoolReleaseConfirmation";
import {
  executeRelease,
  listReleases,
  prepareRelease,
  releaseCommands,
  type ReleaseAction,
  type ReleaseConfirmation,
  type ReleaseView,
  type ReleaseJob,
} from "./AccountPoolReleasesApi";

const actionTitles: Record<ReleaseAction["action"], string> = {
  apply: "应用备份版本",
  delete: "删除镜像备份",
  note: "保存版本备注",
  guide: "保存回退说明",
  scan: "扫描并备份本机镜像",
  deploy: "部署新版本",
  recover: "恢复运行版本",
};
const jobLabels = {
  queued: "等待执行",
  running: "执行中",
  succeeded: "已完成",
  failed: "执行失败",
  recovered: "切换失败，已恢复原版本",
  interrupted: "任务已中断",
};

const jobIsPending = (job: ReleaseJob | null | undefined) => job?.status === "queued" || job?.status === "running";
const selectedVersion = (data: ReleaseView | undefined, id: string | null) =>
  data?.versions.find((version) => version.pair.id === id) ??
  data?.versions.find((version) => version.current) ??
  data?.versions[0];

export function AccountPoolReleasesPanel({ accessToken }: { accessToken: string }) {
  const client = useQueryClient();
  const queryKey = ["account-pool", "releases", accessToken];
  const queryOptions = {
    queryKey,
    queryFn: () => listReleases(accessToken),
    refetchInterval: (state: Query<ReleaseView>) => (jobIsPending(state.state.data?.job) ? 2000 : 15000),
    retry: false,
  };
  const query = useQuery(queryOptions);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [guide, setGuide] = useState<string | null>(null);
  const [pending, setPending] = useState<{
    confirmation: ReleaseConfirmation;
    description: string;
    before?: string;
  } | null>(null);
  const data = query.data;
  const selected = selectedVersion(data, selectedId);
  const commandOptions = {
    queryKey: ["account-pool", "release-commands", accessToken, selected?.pair.id],
    queryFn: () => releaseCommands(accessToken, selected!.pair.id),
    enabled: !!selected,
    retry: false,
  };
  const commands = useQuery(commandOptions);
  const refresh = () =>
    Promise.all([
      client.invalidateQueries({ queryKey }),
      client.invalidateQueries({ queryKey: ["account-pool", "release-commands", accessToken] }),
    ]);
  const preparation = useMutation({
    mutationFn: ({ action }: { action: ReleaseAction; description: string; before?: string }) =>
      prepareRelease(accessToken, action),
    onSuccess: (confirmation, context) =>
      setPending({ confirmation, description: context.description, before: context.before }),
    onError: (error) => {
      toast.fromError(error);
      void refresh();
    },
  });
  const execution = useMutation({
    mutationFn: (token: string) => executeRelease(accessToken, token),
    onSuccess: (job) => {
      setPending(null);
      setNote(null);
      setGuide(null);
      client.setQueryData(queryKey, data ? { ...data, job } : data);
      toast.success("任务已提交，执行结果将在此页显示");
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
  if (!data)
    return (
      <Card>
        <CardHeader>
          <CardTitle>版本管理</CardTitle>
          <CardDescription>
            {query.isError ? "版本管理暂时不可用，请检查部署服务是否启用" : "正在读取运行版本与备份…"}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {query.isError && (
            <p role="alert" className="mb-3 text-sm text-destructive">
              {query.error.message}
            </p>
          )}
          <Button variant="outline" onClick={() => void query.refetch()} disabled={query.isFetching}>
            重新读取
          </Button>
        </CardContent>
      </Card>
    );
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">版本管理</h2>
          <p className="mt-1 text-sm text-muted-foreground">管理 LiteLLM 与号池 Manager 的配套镜像备份</p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            disabled={busy || !data.current}
            onClick={() =>
              prepare(
                { action: "scan" },
                "将当前版本和本机已有的配套历史镜像保存为归档。已有完整备份会跳过，过程可能需要几分钟。",
              )
            }
          >
            扫描并备份
          </Button>
          <Button variant="ghost" aria-label="刷新版本列表" disabled={query.isFetching} onClick={() => void refresh()}>
            <RefreshCw className={query.isFetching ? "animate-spin" : ""} />
          </Button>
        </div>
      </div>
      {query.isError && (
        <p role="alert" className="rounded-lg border border-destructive/30 p-3 text-sm text-destructive">
          暂时无法连接，保留上次数据并继续重试。服务切换期间可能短暂中断。
        </p>
      )}
      {data.problems?.map((problem) => (
        <p key={problem} role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      ))}
      {data.job && (
        <div role="status" className="rounded-xl border bg-muted/30 px-4 py-3 text-sm">
          <p className="font-medium">
            {jobLabels[data.job.status]} · {data.job.phase}
          </p>
          {data.job.message && <p className="mt-1 text-muted-foreground">{data.job.message}</p>}
        </div>
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
              <CardTitle>尚无可用版本</CardTitle>
              <CardDescription>请先检查运行版本，或扫描本机镜像生成备份</CardDescription>
            </CardHeader>
          </Card>
        )}
        <ReleaseStorageCard data={data} />
      </div>
      <ReleaseGuideCard data={data} guide={guide} setGuide={setGuide} busy={busy} prepare={prepare} />
      {selected && <ReleaseCommandsCard selected={selected} commands={commands.data} error={commands.isError} />}
      {pending && (
        <AccountPoolReleaseConfirmation
          key={pending.confirmation.token}
          {...pending}
          title={actionTitles[pending.confirmation.action.action]}
          busy={execution.isPending}
          onClose={() => setPending(null)}
          onConfirm={() => execution.mutate(pending.confirmation.token)}
        />
      )}
    </div>
  );
}
