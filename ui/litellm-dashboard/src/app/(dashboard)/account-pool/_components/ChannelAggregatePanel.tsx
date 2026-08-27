// 本文件展示单个渠道的配置、解析、健康、路由和最近事件聚合详情。
"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Loader2 } from "lucide-react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

import { accountPoolKeys, getChannelAggregate } from "../api";
import { reasonLabelWithCode } from "../reasonLabels";
import type { ChannelAggregateDetail, DetailSection, JsonValue, RoutingTableEntry } from "../types";

interface ChannelAggregatePanelProps {
  accessToken: string;
  channelId: string;
  onOpenChannelEvents: (channelId: string) => void;
}

const formatDate = (value: string | null): string =>
  value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "暂无";

const formatJson = (value: JsonValue): string => JSON.stringify(value, null, 2);

const routeStatus = (route: RoutingTableEntry): string =>
  route.available ? "可调度" : reasonLabelWithCode(route.reason_code ?? route.unavailable_reason ?? "不可调度");

const columnDividerClass =
  "[&_th:not(:last-child)]:border-r [&_th:not(:last-child)]:border-border/70 [&_td:not(:last-child)]:border-r [&_td:not(:last-child)]:border-border/70";

export default function ChannelAggregatePanel({
  accessToken,
  channelId,
  onOpenChannelEvents,
}: ChannelAggregatePanelProps) {
  const query = useQuery({
    queryKey: accountPoolKeys.aggregate(channelId),
    queryFn: () => getChannelAggregate(accessToken, channelId),
  });

  if (query.isLoading) {
    return (
      <div className="flex min-h-72 items-center justify-center">
        <Loader2 className="animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (query.isError || !query.data) {
    return (
      <div className="flex min-h-72 flex-col items-center justify-center gap-2 text-center">
        <AlertTriangle className="size-7 text-muted-foreground" />
        <p className="text-sm font-medium">渠道综合详情暂不可用</p>
      </div>
    );
  }

  return <AggregateContent detail={query.data} onOpenChannelEvents={onOpenChannelEvents} />;
}

function AggregateContent({
  detail,
  onOpenChannelEvents,
}: {
  detail: ChannelAggregateDetail;
  onOpenChannelEvents: (channelId: string) => void;
}) {
  const overview = detail.overview.data;
  const parser = detail.parser.data;
  const health = detail.health.data;
  return (
    <div className="min-w-0 space-y-6">
      <div className="grid overflow-hidden rounded-md border sm:grid-cols-2 xl:grid-cols-4">
        <SummaryItem label="调度资格" value={overview?.schedulable_models.length ? "可调度" : "不可调度"} />
        <SummaryItem label="运行健康" value={overview?.runtime?.health ?? detail.health.failure?.code ?? "未知"} />
        <SummaryItem
          label="当前并发"
          value={overview?.runtime ? `${overview.runtime.inflight} / ${overview.runtime.max_concurrency}` : "未知"}
        />
        <SummaryItem label="最近请求" value={formatDate(overview?.activity.last_request_at ?? null)} />
      </div>

      <section className="border-t pt-5">
        <SectionHeading title="基础配置与 Deployment 绑定" status="loaded" />
        <div className="grid gap-x-8 gap-y-3 border-y py-4 text-sm sm:grid-cols-2 xl:grid-cols-4">
          <DetailValue label="渠道 ID" value={detail.channel.channel_id} />
          <DetailValue label="Provider" value={detail.channel.provider} />
          <DetailValue label="分组" value={detail.channel.group ?? "未设置"} />
          <DetailValue label="Key 摘要" value={detail.channel.key_mask ?? "无"} />
          <DetailValue label="Base URL" value={detail.channel.base_url_display} />
          <DetailValue label="优先级" value={String(detail.channel.priority)} />
          <DetailValue label="调度权重" value={String(detail.channel.weight)} />
          <DetailValue label="最大并发" value={String(detail.channel.max_concurrency)} />
        </div>
        <div className="overflow-x-auto rounded-md border">
          <Table className={columnDividerClass}>
            <TableHeader>
              <TableRow>
                <TableHead>对外模型</TableHead>
                <TableHead>Provider 模型</TableHead>
                <TableHead>Deployment</TableHead>
                <TableHead>所有权</TableHead>
                <TableHead>状态</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {detail.channel.bindings.map((binding) => (
                <TableRow key={binding.binding_id ?? `${binding.public_model}:${binding.provider_model}`}>
                  <TableCell>{binding.public_model}</TableCell>
                  <TableCell className="break-all">{binding.provider_model ?? "未设置"}</TableCell>
                  <TableCell className="break-all">{binding.litellm_deployment_id ?? "待同步"}</TableCell>
                  <TableCell>{binding.ownership === "pool_managed" ? "号池管理" : "外部管理"}</TableCell>
                  <TableCell>
                    <Badge variant={binding.enabled ? "secondary" : "outline"}>
                      {binding.enabled ? "启用" : "停用"}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </section>

      <section className="border-t pt-5">
        <SectionHeading title="套餐、按量和解析状态" section={detail.parser} />
        {parser && (
          <div className="grid gap-4 lg:grid-cols-2">
            <JsonBlock title="套餐与额度窗口" value={parser.effective_result.subscription} />
            <JsonBlock title="按量分组与标准化价格" value={parser.effective_result.metered} />
            <JsonBlock title="未解析字段" value={parser.effective_result.unresolved_fields} />
            <JsonBlock title="可执行计费路由" value={parser.effective_result.billing_routes} />
          </div>
        )}
      </section>

      <section className="border-t pt-5">
        <SectionHeading title="健康、冷却与资格" section={detail.health} />
        {health && (
          <div className="grid gap-x-8 gap-y-3 border-y py-4 text-sm sm:grid-cols-2 xl:grid-cols-4">
            <DetailValue label="健康状态" value={health.runtime.health} />
            <DetailValue label="当前并发" value={`${health.runtime.inflight} / ${health.runtime.max_concurrency}`} />
            <DetailValue label="排除数量" value={String(health.exclusions.length)} />
            <DetailValue label="持久化事实" value={health.persistence_available ? "可用" : "仅运行态"} />
          </div>
        )}
      </section>

      <RouteSection section={detail.routes} />
      <EventSection detail={detail} onOpenChannelEvents={onOpenChannelEvents} />
    </div>
  );
}

function RouteSection({ section }: { section: DetailSection<RoutingTableEntry[]> }) {
  return (
    <section className="border-t pt-5">
      <SectionHeading title="模型调度位置" section={section} />
      {section.data && (
        <div className="overflow-x-auto rounded-md border">
          <Table className={columnDividerClass}>
            <TableHeader>
              <TableRow>
                <TableHead>顺序</TableHead>
                <TableHead>模型</TableHead>
                <TableHead>Deployment</TableHead>
                <TableHead>策略</TableHead>
                <TableHead>资格</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {section.data.map((route) => (
                <TableRow key={`${route.public_model}:${route.deployment_id}`}>
                  <TableCell>{route.position ?? "动态"}</TableCell>
                  <TableCell>{route.public_model}</TableCell>
                  <TableCell className="break-all">{route.deployment_id}</TableCell>
                  <TableCell>{route.strategy ?? "默认"}</TableCell>
                  <TableCell>
                    <Badge variant={route.available ? "secondary" : "destructive"}>{routeStatus(route)}</Badge>
                  </TableCell>
                </TableRow>
              ))}
              {section.data.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} className="h-20 text-center text-muted-foreground">
                    当前没有运行路由
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  );
}

function EventSection({
  detail,
  onOpenChannelEvents,
}: {
  detail: ChannelAggregateDetail;
  onOpenChannelEvents: (channelId: string) => void;
}) {
  return (
    <section className="border-t pt-5">
      <SectionHeading
        title="最近事件"
        section={detail.events}
        action={
          <Button variant="outline" size="sm" onClick={() => onOpenChannelEvents(detail.channel.channel_id)}>
            查看完整日志
          </Button>
        }
      />
      {detail.events.data && (
        <div className="overflow-x-auto rounded-md border">
          <Table className={columnDividerClass}>
            <TableHeader>
              <TableRow>
                <TableHead>时间</TableHead>
                <TableHead>类型</TableHead>
                <TableHead>结果</TableHead>
                <TableHead>原因</TableHead>
                <TableHead>Request ID</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {detail.events.data.map((event) => (
                <TableRow key={event.event_id}>
                  <TableCell className="whitespace-nowrap">{formatDate(event.occurred_at)}</TableCell>
                  <TableCell>{event.event_type}</TableCell>
                  <TableCell>{event.outcome}</TableCell>
                  <TableCell>{event.reason_code ? reasonLabelWithCode(event.reason_code) : "无"}</TableCell>
                  <TableCell className="break-all">{event.request_id ?? "无"}</TableCell>
                </TableRow>
              ))}
              {detail.events.data.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} className="h-20 text-center text-muted-foreground">
                    暂无事件
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  );
}

function SectionHeading<T>({
  title,
  section,
  status,
  action,
}: {
  title: string;
  section?: DetailSection<T>;
  status?: "loaded";
  action?: ReactNode;
}) {
  const loaded = status === "loaded" || section?.status === "loaded";
  return (
    <div className="mb-2 flex flex-wrap items-center gap-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      <Badge variant={loaded ? "secondary" : "outline"}>{loaded ? "已加载" : section?.failure?.code ?? "不可用"}</Badge>
      {action && <div className="ml-auto">{action}</div>}
    </div>
  );
}

function DetailValue({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <span className="block text-xs text-muted-foreground">{label}</span>
      <span className="mt-1 block break-all">{value}</span>
    </div>
  );
}

function JsonBlock({ title, value }: { title: string; value: JsonValue }) {
  return (
    <div className="min-w-0">
      <h4 className="mb-2 text-xs font-medium text-muted-foreground">{title}</h4>
      <pre className="max-h-72 overflow-auto rounded-md bg-muted p-3 text-xs whitespace-pre-wrap break-all">
        {formatJson(value)}
      </pre>
    </div>
  );
}

function SummaryItem({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="min-h-20 border-b px-4 py-3 last:border-b-0 sm:[&:nth-child(odd)]:border-r xl:border-r xl:border-b-0 xl:last:border-r-0">
      <span className="block text-xs text-muted-foreground">{label}</span>
      <strong className="mt-1 block break-words text-sm font-semibold">{value}</strong>
    </div>
  );
}
