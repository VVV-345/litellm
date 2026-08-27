// 本文件展示渠道当前健康、冷却、最近活动和脱敏健康事件。
"use client";

import { useQuery } from "@tanstack/react-query";
import { Activity, AlertTriangle, CheckCircle2, Clock3 } from "lucide-react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

import { accountPoolKeys, getChannelHealth } from "../api";
import {
  accountPoolTableColumnDividerClass,
  formatAccountPoolDateTime,
  formatAccountPoolEpoch,
  healthPresentation,
} from "../accountPoolPresentation";
import type { HealthEventRecord, HealthExclusion } from "../types";
import { AccountPoolQueryState } from "./AccountPoolPanel";

interface HealthStatusPanelProps {
  accessToken: string;
  channelId: string;
}

const reasonLabel: Record<string, string> = {
  credential_invalid: "凭证无效",
  permission_denied: "权限不足",
  model_not_found: "模型不存在",
  rate_limited: "请求限流",
  rate_limit_unknown: "未知限流",
  concurrency_limited: "并发受限",
  balance_signal_unscoped: "余额不足",
  quota_signal_unscoped: "额度不足",
  upstream_unavailable: "上游不可用",
  transport_failure: "连接失败",
};

const latestDate = (values: Array<string | null>) =>
  values.reduce<string | null>((latest, value) => (!value || (latest && latest >= value) ? latest : value), null);
const eventSource = (record: HealthEventRecord) =>
  record.health.source === "active_probe" ? `主动探测 · ${record.health.probe_trigger}` : "真实请求";
const exclusionSubject = (exclusion: HealthExclusion) =>
  exclusion.billing_route_id ?? exclusion.deployment_id ?? exclusion.model ?? exclusion.account_id;

export default function HealthStatusPanel({ accessToken, channelId }: HealthStatusPanelProps) {
  const query = useQuery({
    queryKey: accountPoolKeys.health(channelId),
    queryFn: () => getChannelHealth(accessToken, channelId),
  });

  if (query.isLoading) {
    return <AccountPoolQueryState kind="loading" message="正在读取健康与冷却状态" />;
  }
  if (query.isError || !query.data) {
    return <AccountPoolQueryState kind="error" message="健康详情暂不可用" />;
  }

  const detail = query.data;
  const runtime = detail.runtime;
  const lastRequestAt = latestDate(detail.activities.map((activity) => activity.last_request_at));
  const lastProbeAt = latestDate(detail.activities.map((activity) => activity.last_probe_at));

  return (
    <div className="min-w-0 space-y-6">
      <div className="grid overflow-hidden rounded-md border sm:grid-cols-2 xl:grid-cols-4">
        <SummaryItem
          icon={runtime.health === "healthy" ? <CheckCircle2 /> : <AlertTriangle />}
          label="当前状态"
          value={healthPresentation[runtime.health].label}
        />
        <SummaryItem icon={<Activity />} label="当前并发" value={`${runtime.inflight} / ${runtime.max_concurrency}`} />
        <SummaryItem icon={<Clock3 />} label="最近请求" value={formatAccountPoolDateTime(lastRequestAt, "-")} />
        <SummaryItem icon={<Clock3 />} label="最近探测" value={formatAccountPoolDateTime(lastProbeAt, "-")} />
      </div>

      <section>
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold">当前排除与冷却</h3>
          <Badge variant="outline">{detail.exclusions.length}</Badge>
          {!detail.persistence_available && <Badge variant="outline">仅运行时数据</Badge>}
        </div>
        <div className="overflow-x-auto rounded-md border">
          <Table className={accountPoolTableColumnDividerClass}>
            <TableHeader>
              <TableRow>
                <TableHead>范围</TableHead>
                <TableHead>对象</TableHead>
                <TableHead>来源</TableHead>
                <TableHead>原因</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>恢复时间</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {detail.exclusions.map((exclusion) => (
                <TableRow key={`${exclusion.source}:${exclusion.scope}:${exclusionSubject(exclusion)}`}>
                  <TableCell>{exclusion.scope}</TableCell>
                  <TableCell className="max-w-56 whitespace-normal break-all">{exclusionSubject(exclusion)}</TableCell>
                  <TableCell>{exclusion.source}</TableCell>
                  <TableCell>{reasonLabel[exclusion.reason_code] ?? exclusion.reason_code}</TableCell>
                  <TableCell>
                    <Badge variant={exclusion.state === "active" ? "destructive" : "outline"}>{exclusion.state}</Badge>
                  </TableCell>
                  <TableCell>{formatAccountPoolEpoch(exclusion.retry_at)}</TableCell>
                </TableRow>
              ))}
              {detail.exclusions.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} className="h-20 text-center text-muted-foreground">
                    当前没有生效中的排除
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </section>

      <section>
        <div className="mb-2 flex items-center gap-2">
          <h3 className="text-sm font-semibold">最近健康事件</h3>
          <Badge variant="outline">{detail.events.length}</Badge>
        </div>
        <div className="overflow-x-auto rounded-md border">
          <Table className={accountPoolTableColumnDividerClass}>
            <TableHeader>
              <TableRow>
                <TableHead>时间</TableHead>
                <TableHead>来源</TableHead>
                <TableHead>模型 / Deployment</TableHead>
                <TableHead>结果</TableHead>
                <TableHead>原因</TableHead>
                <TableHead className="text-right">延迟</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {detail.events.map((record) => (
                <TableRow key={record.event.event_id}>
                  <TableCell>{formatAccountPoolDateTime(record.event.occurred_at, "-")}</TableCell>
                  <TableCell>{eventSource(record)}</TableCell>
                  <TableCell className="max-w-64 whitespace-normal break-all">
                    <div>{record.event.model_id}</div>
                    <div className="text-xs text-muted-foreground">{record.event.deployment_id}</div>
                  </TableCell>
                  <TableCell>
                    <Badge variant={record.health.outcome === "succeeded" ? "secondary" : "destructive"}>
                      {record.health.outcome === "succeeded" ? "成功" : "失败"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {reasonLabel[record.event.reason_code ?? ""] ?? record.event.reason_code ?? "-"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {record.event.safe_details.latency_ms === null
                      ? "-"
                      : `${Math.round(record.event.safe_details.latency_ms)} ms`}
                  </TableCell>
                </TableRow>
              ))}
              {detail.events.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} className="h-20 text-center text-muted-foreground">
                    暂无持久化健康事件
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </section>
    </div>
  );
}

const SummaryItem = ({ icon, label, value }: { icon: ReactNode; label: string; value: string }) => (
  <div className="flex min-h-24 items-center gap-3 border-b px-4 py-3 last:border-b-0 sm:[&:nth-child(odd)]:border-r xl:border-r xl:border-b-0 xl:last:border-r-0">
    <span className="text-muted-foreground [&>svg]:size-5">{icon}</span>
    <span className="min-w-0">
      <span className="block text-xs text-muted-foreground">{label}</span>
      <span className="mt-1 block break-words text-sm font-medium">{value}</span>
    </span>
  </div>
);
