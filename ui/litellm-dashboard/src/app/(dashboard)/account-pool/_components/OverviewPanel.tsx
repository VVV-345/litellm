// 本文件展示渠道目录、解析数据、健康状态、额度和最近活动的聚合总览。
"use client";

import { useQuery } from "@tanstack/react-query";
import { ListFilter, Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

import { accountPoolKeys, getOverview } from "../api";
import {
  accountPoolTableColumnDividerClass,
  formatAccountPoolDateTime,
  formatAccountPoolNumber,
  healthPresentation,
  parserOverviewLabels,
} from "../accountPoolPresentation";
import { reasonLabelWithCode } from "../reasonLabels";
import type { ChannelOverview } from "../types";
import CapacityMeter from "./CapacityMeter";

const parserSummary = (channel: ChannelOverview): string => {
  const subscription = channel.parser.subscription;
  const metered = channel.parser.metered;
  if (subscription && metered) return `${subscription.plan_name ?? "套餐"} / ${metered.group_count} 个按量分组`;
  if (subscription) return subscription.plan_name ?? "已识别套餐";
  if (metered) return `${metered.group_count} 个按量分组`;
  return parserOverviewLabels[channel.parser.state];
};

const quotaSummary = (channel: ChannelOverview): string => {
  if (!channel.runtime) return "运行态未投影";
  const quota = channel.runtime.quota;
  return `${formatAccountPoolNumber(quota.total)} ${quota.unit}`;
};

const parserIdentity = (channel: ChannelOverview): string => {
  if (!channel.parser.parser_id) return parserOverviewLabels[channel.parser.state];
  const version = channel.parser.parser_version ? ` ${channel.parser.parser_version}` : "";
  return `${channel.parser.parser_id}${version} · ${channel.parser.status ?? "unknown"}`;
};

const subscriptionBalance = (channel: ChannelOverview): string | null => {
  const subscription = channel.parser.subscription;
  if (!subscription || subscription.balance === null) return null;
  return `${formatAccountPoolNumber(subscription.balance)} ${subscription.currency ?? ""}`.trim();
};

interface OverviewPanelProps {
  accessToken: string;
  onOpenChannelEvents?: (channelId: string) => void;
}

export default function OverviewPanel({ accessToken, onOpenChannelEvents }: OverviewPanelProps) {
  const overviewQuery = useQuery({
    queryKey: accountPoolKeys.overview(),
    queryFn: () => getOverview(accessToken),
  });

  if (overviewQuery.isLoading) {
    return (
      <div className="flex min-h-80 items-center justify-center">
        <Loader2 className="animate-spin" />
      </div>
    );
  }
  if (overviewQuery.isError || !overviewQuery.data) {
    return (
      <div className="rounded-md border border-destructive/30 px-4 py-10 text-center text-sm text-destructive">
        无法读取聚合总览，请检查 Account Pool 数据库和运行服务
      </div>
    );
  }

  const overview = overviewQuery.data;
  return (
    <div className="min-w-0 space-y-4">
      <div className="grid border-y sm:grid-cols-2 lg:grid-cols-4">
        <SummaryMetric
          label="渠道"
          value={`${overview.schedulable_count}/${overview.channel_count} 可调度`}
          tone={overview.schedulable_count === overview.channel_count ? "emerald" : "amber"}
        />
        <SummaryMetric
          label="模型"
          value={`${overview.schedulable_model_count}/${overview.configured_model_count} 可调度`}
          tone="sky"
        />
        <SummaryMetric label="健康" value={`${overview.healthy_count}/${overview.channel_count} 正常`} tone="emerald" />
        <SummaryMetric label="并发" value={`${overview.inflight}/${overview.max_concurrency}`} tone="violet" />
      </div>

      <section className="min-w-0 overflow-hidden rounded-md border bg-background">
        <div className="border-b px-4 py-3">
          <h2 className="text-sm font-semibold">渠道运行总览</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">配置、解析和实时调度资格的统一视图</p>
        </div>
        <Table className={accountPoolTableColumnDividerClass}>
          <TableHeader>
            <TableRow>
              <TableHead>渠道</TableHead>
              <TableHead>配置模型</TableHead>
              <TableHead>解析</TableHead>
              <TableHead>健康与资格</TableHead>
              <TableHead>并发与额度</TableHead>
              <TableHead>最近活动</TableHead>
              <TableHead className="w-12">
                <span className="sr-only">操作</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {overview.channels.map((channel) => (
              <TableRow key={channel.channel_id}>
                <TableCell className="max-w-56 whitespace-normal align-top">
                  <strong className="block">{channel.display_name}</strong>
                  <span className="mt-1 block break-all text-xs text-muted-foreground">
                    {channel.provider} · {channel.key_mask ?? "无凭证摘要"}
                  </span>
                </TableCell>
                <TableCell className="align-top">
                  {channel.schedulable_models.length}/{channel.configured_models.length} 可调度
                  <span className="mt-1 block max-w-52 whitespace-normal text-xs text-muted-foreground">
                    {channel.configured_models.join("、") || "暂无模型"}
                  </span>
                </TableCell>
                <TableCell className="align-top">
                  <Badge variant={channel.parser.state === "loaded" ? "secondary" : "outline"}>
                    {parserOverviewLabels[channel.parser.state]}
                  </Badge>
                  <span className="mt-1 block max-w-52 whitespace-normal text-xs text-muted-foreground">
                    {parserSummary(channel)}
                  </span>
                  <span className="mt-1 block max-w-52 break-all whitespace-normal text-xs text-muted-foreground">
                    {parserIdentity(channel)}
                  </span>
                </TableCell>
                <TableCell className="align-top">
                  <Badge
                    variant="outline"
                    className={
                      channel.schedulable_models.length > 0
                        ? "border-emerald-300 bg-emerald-50 text-emerald-800"
                        : "border-red-300 bg-red-50 text-red-800"
                    }
                  >
                    {channel.schedulable_models.length > 0
                      ? "可调度"
                      : channel.unavailable_reason_codes.map(reasonLabelWithCode).join("、") ||
                        (channel.runtime?.reason_code ? reasonLabelWithCode(channel.runtime.reason_code) : null) ||
                        healthPresentation[channel.runtime?.health ?? "unknown"].label}
                  </Badge>
                  {channel.runtime ? (
                    <Badge variant="outline" className={`mt-1 ${healthPresentation[channel.runtime.health].className}`}>
                      {healthPresentation[channel.runtime.health].label}
                    </Badge>
                  ) : (
                    <span className="mt-1 block text-xs text-muted-foreground">运行态未投影</span>
                  )}
                </TableCell>
                <TableCell className="align-top tabular-nums">
                  {channel.runtime ? `${channel.runtime.inflight}/${channel.runtime.max_concurrency}` : "未知"}
                  {channel.runtime && (
                    <CapacityMeter
                      value={channel.runtime.inflight / channel.runtime.max_concurrency}
                      label="并发占用"
                      tone="violet"
                    />
                  )}
                  <span className="mt-1 block text-xs text-muted-foreground">余额/额度 {quotaSummary(channel)}</span>
                  {subscriptionBalance(channel) && (
                    <span className="mt-1 block text-xs text-muted-foreground">
                      套餐余额 {subscriptionBalance(channel)}
                    </span>
                  )}
                </TableCell>
                <TableCell className="align-top">
                  {formatAccountPoolDateTime(channel.activity.last_request_at)}
                  <span className="mt-1 block text-xs text-muted-foreground">
                    {channel.activity.persistence_available ? "最近请求" : "活动数据不可用"}
                  </span>
                </TableCell>
                <TableCell className="align-top">
                  {onOpenChannelEvents && (
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      title="查看渠道日志"
                      onClick={() => onOpenChannelEvents(channel.channel_id)}
                    >
                      <ListFilter />
                      <span className="sr-only">查看渠道日志</span>
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
            {overview.channels.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="h-28 text-center text-muted-foreground">
                  PostgreSQL 渠道目录中暂无数据
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </section>
    </div>
  );
}

function SummaryMetric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "emerald" | "amber" | "sky" | "violet";
}) {
  const dotClass = {
    emerald: "bg-emerald-500",
    amber: "bg-amber-500",
    sky: "bg-sky-500",
    violet: "bg-violet-500",
  }[tone];
  return (
    <div className="px-4 py-3 sm:border-r sm:last:border-r-0">
      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <span className={`size-1.5 rounded-full ${dotClass}`} />
        {label}
      </span>
      <strong className="mt-1 block text-lg font-semibold tabular-nums">{value}</strong>
    </div>
  );
}
