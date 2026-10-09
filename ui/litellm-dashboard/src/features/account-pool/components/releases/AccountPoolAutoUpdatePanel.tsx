"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { ApiError } from "@/lib/http/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { migratedHref } from "@/utils/migratedPages";
import {
  checkAutoUpdate,
  getAutoUpdate,
  saveAutoUpdate,
  type AutoUpdateSettings,
  type AutoUpdateView,
} from "../../api/AccountPoolReleasesApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";

const statusLabels: Record<AutoUpdateView["status"], string> = {
  disabled: "已关闭",
  waiting: "等待检查",
  checking: "正在检查",
  current: "已是最新版本",
  available: "发现新版本",
  queued: "等待部署",
  deploying: "正在部署",
  paused: "已暂停",
  error: "检查失败",
};
const checkedTime = (value: number | null | undefined) =>
  value == null ? "暂无" : new Date(value * 1000).toLocaleString("zh-CN");
type Draft = { enabled: boolean; interval: string; revision: number };

export function AccountPoolAutoUpdatePanel({ accessToken }: { accessToken: string }) {
  const client = useQueryClient();
  const queryKey = accountPoolQueryKeys.autoUpdate(accessToken);
  const queryOptions = { queryKey, queryFn: () => getAutoUpdate(accessToken), refetchInterval: 15000, retry: false };
  const query = useQuery(queryOptions);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const save = useMutation({
    mutationFn: (body: AutoUpdateSettings) => saveAutoUpdate(accessToken, body),
    onMutate: () => client.cancelQueries({ queryKey }),
    onSuccess: (data) => {
      client.setQueryData(queryKey, data);
      setDraft(null);
      setAcknowledged(false);
    },
  });
  const check = useMutation({
    mutationFn: () => checkAutoUpdate(accessToken),
    onMutate: () => client.cancelQueries({ queryKey }),
    onSuccess: (data) => {
      client.setQueryData(queryKey, data);
      void client.invalidateQueries({ queryKey: accountPoolQueryKeys.releases(accessToken) });
    },
  });
  const reload = useMutation({
    mutationFn: () => getAutoUpdate(accessToken),
    onSuccess: (data) => {
      client.setQueryData(queryKey, data);
      setDraft(null);
      setAcknowledged(false);
      save.reset();
      check.reset();
    },
  });
  const data = query.data;
  const busy = save.isPending || check.isPending || reload.isPending;
  const form = draft ?? {
    enabled: data?.enabled ?? false,
    interval: String(data?.interval_minutes ?? 5),
    revision: data?.revision ?? 0,
  };
  const interval = Number(form.interval);
  const validInterval = Number.isInteger(interval) && interval >= 5 && interval <= 1440;
  const enableAuthorized = acknowledged && data?.database_backups_enabled;
  const scheduleAuthorized = !form.enabled || enableAuthorized;
  const canSave = draft && validInterval && scheduleAuthorized;
  const conflict = save.error instanceof ApiError && save.error.status === 409;
  const error = save.error ?? check.error ?? reload.error;
  const edit = (values: Partial<Draft>) => {
    setDraft({ ...form, ...values });
    save.reset();
    check.reset();
  };
  const submit = () => {
    check.reset();
    const settings: AutoUpdateSettings = {
      enabled: form.enabled,
      interval_minutes: interval,
      revision: form.revision,
      acknowledge_downtime: form.enabled && acknowledged,
    };
    save.mutate(settings);
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle>自动更新镜像</CardTitle>
        <CardDescription>定时检查 GHCR 中 CLIProxyAPI分支 的配套镜像，开启后自动备份并部署新版本</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {query.isPending && <p role="status">正在读取自动更新设置…</p>}
        {query.isError && (
          <p role="alert" className="text-sm text-destructive">
            {query.error instanceof ApiError && query.error.status === 404
              ? "自动更新接口不可用（404），请确认部署服务已启用并升级到支持自动更新的版本"
              : `自动更新状态读取失败：${query.error.message}`}
          </p>
        )}
        {data && (
          <>
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="auto-update-enabled">自动部署新镜像</Label>
              <Switch
                id="auto-update-enabled"
                checked={form.enabled}
                disabled={busy || (!form.enabled && !data.database_backups_enabled)}
                onCheckedChange={(enabled) => {
                  edit({ enabled });
                  setAcknowledged(false);
                }}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="auto-update-interval">检查间隔（分钟）</Label>
              <Input
                id="auto-update-interval"
                type="number"
                min={5}
                max={1440}
                step={1}
                value={form.interval}
                disabled={busy}
                aria-invalid={!validInterval}
                onChange={(event) => edit({ interval: event.target.value })}
              />
              {!validInterval && (
                <p role="alert" className="text-sm text-destructive">
                  检查间隔须为 5 到 1440 的整数
                </p>
              )}
            </div>
            <p className="text-xs leading-5 text-muted-foreground">
              自动部署会暂停业务并备份数据库，可能短暂中断请求。关闭后立即检查只检测镜像，不部署；尚未执行的自动部署会取消，已开始执行的部署会完成或安全恢复。
            </p>
            {!data.database_backups_enabled && (
              <p className="text-sm text-muted-foreground">请先配置数据库备份，才能开启自动部署</p>
            )}
            {form.enabled && (
              <div className="flex items-start gap-2">
                <Checkbox
                  id="auto-update-acknowledgement"
                  checked={acknowledged}
                  onCheckedChange={setAcknowledged}
                  disabled={busy}
                />
                <Label htmlFor="auto-update-acknowledgement" className="text-sm leading-5">
                  我确认自动部署会暂停业务、备份数据库，并允许服务短暂中断
                </Label>
              </div>
            )}
            <dl className="space-y-2 text-xs">
              <div>
                <dt className="text-muted-foreground">当前状态</dt>
                <dd>{statusLabels[data.status]}</dd>
              </div>
              {data.message && (
                <div>
                  <dt className="sr-only">状态说明</dt>
                  <dd>{data.message}</dd>
                </div>
              )}
              <div>
                <dt className="text-muted-foreground">上次检查</dt>
                <dd>{checkedTime(data.last_checked_at)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">下次检查</dt>
                <dd>{checkedTime(data.next_check_at)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">运行提交 / 候选提交</dt>
                <dd className="break-all">
                  {data.current_commit ?? "未知"} / {data.candidate_commit ?? "暂无"}
                </dd>
              </div>
              {data.last_job_id && (
                <div>
                  <dt className="text-muted-foreground">最近部署任务</dt>
                  <dd className="break-all">{data.last_job_id}</dd>
                </div>
              )}
            </dl>
            <div className="flex flex-wrap gap-2">
              <Button disabled={busy || !canSave} onClick={submit}>
                {save.isPending ? "正在保存…" : "保存自动更新设置"}
              </Button>
              <Button
                variant="outline"
                disabled={busy || ["checking", "queued", "deploying"].includes(data.status)}
                onClick={() => {
                  save.reset();
                  check.mutate();
                }}
              >
                {check.isPending ? "正在提交…" : "立即检查"}
              </Button>
            </div>
          </>
        )}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {conflict ? `保存未完成，编辑内容已保留。请核对错误并重新载入设置：${error.message}` : error.message}
          </p>
        )}
        {save.isSuccess && (
          <p role="status" className="text-sm">
            自动更新设置已保存
          </p>
        )}
        {check.isSuccess && (
          <p role="status" className="text-sm">
            检查请求已提交，结果将自动刷新
          </p>
        )}
        {(conflict || query.isError) && (
          <Button variant="outline" disabled={busy} onClick={() => reload.mutate()}>
            重新载入设置
          </Button>
        )}
        <Link
          className="block text-sm text-primary hover:underline"
          href={`${migratedHref("account-pool")}?tab=releases`}
        >
          查看版本与部署日志
        </Link>
      </CardContent>
    </Card>
  );
}
