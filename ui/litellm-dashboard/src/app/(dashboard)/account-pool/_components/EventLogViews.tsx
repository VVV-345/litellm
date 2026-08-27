"use client";

import { ChevronLeft, ChevronRight, Eye, Loader2, RotateCcw, Search } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

import { accountPoolTableColumnDividerClass, formatAccountPoolDateTime } from "../accountPoolPresentation";
import { reasonLabelWithCode } from "../reasonLabels";
import type { EventLogEntry, EventLogFilters, EventQueryOutcome } from "../types";
import { AccountPoolPanel, AccountPoolQueryState } from "./AccountPoolPanel";

const eventTypeLabels: Record<string, string> = {
  active_health_probe_result: "主动健康探测",
  passive_health_result: "请求健康结果",
  channel_create: "创建渠道",
  channel_update: "修改渠道",
  channel_import: "导入渠道",
  channel_detach: "解绑渠道",
  channel_delete: "删除渠道",
  channel_delete_external_deployment: "删除外部 Deployment",
  channel_reconcile: "重新同步渠道",
  parser_task_start: "启动解析任务",
  parser_task_completed: "解析任务已完成",
  parser_task_failed: "解析任务失败",
  parser_task_interrupted: "解析任务已中断",
  public_metadata_task_completed: "公开元数据解析完成",
  public_metadata_task_retry_scheduled: "公开元数据等待重试",
  public_metadata_task_failed: "公开元数据解析失败",
  parser_snapshot_exported: "解析快照已导出",
  parser_snapshot_export_retry_scheduled: "解析快照等待重试",
  parser_snapshot_export_failed: "解析快照导出失败",
  sync_retry_succeeded: "后台同步成功",
  sync_retry_failed: "后台同步失败",
  sync_retry_deferred: "后台同步已推迟",
  request_acquired: "请求已分配渠道",
  request_acquire_failed: "请求候选被拒绝",
  request_settled: "请求已结算",
  request_usage_recorded: "请求用量已记录",
  request_released: "请求租约已释放",
  lease_expired: "请求租约已过期",
  eligibility_restriction_activated: "限制已生效",
  eligibility_restriction_updated: "限制已更新",
  eligibility_restriction_cleared: "限制已解除",
  parser_snapshot_import: "导入解析快照",
  parser_override_set: "设置人工修正",
  parser_override_revoke: "撤销人工修正",
  routing_policy_update: "修改路由策略",
  routing_candidate_update: "修改候选设置",
  routing_candidate_delete: "恢复候选设置",
};

const outcomeLabels: Record<EventQueryOutcome, string> = {
  accepted: "已受理",
  succeeded: "成功",
  failed: "失败",
  interrupted: "已中断",
};

const transitionLabels = {
  success: "健康恢复",
  disable: "健康禁用",
  cooldown: "进入冷却",
  observe: "仅记录",
  transient_failure: "暂时异常",
} as const;

const eventTypeOptions = Object.entries(eventTypeLabels);

const eventScope = (event: EventLogEntry): string =>
  event.channel_id ?? event.model_id ?? event.deployment_id ?? "全局事件";

const eventDetails = (event: EventLogEntry): string => {
  if (event.health) return `${event.health.source} · ${event.health.transition} · ${event.health.scope}`;
  if (event.audit) return `${event.audit.actor_action} · ${event.actor_id}`;
  if (event.operational) return `${event.operational.source} · ${event.operational.operation_id}`;
  return event.actor_id;
};

interface EventFiltersProps {
  draftFilters: EventLogFilters;
  isFetching: boolean;
  onChange: (filters: EventLogFilters) => void;
  onApply: () => void;
  onReset: () => void;
}

export function EventFilters({ draftFilters, isFetching, onChange, onApply, onReset }: EventFiltersProps) {
  return (
    <section className="grid gap-3 border-y py-4 sm:grid-cols-2 xl:grid-cols-4">
      <Input
        aria-label="渠道 ID"
        placeholder="渠道 ID"
        value={draftFilters.channel_id ?? ""}
        onChange={(event) => onChange({ ...draftFilters, channel_id: event.target.value })}
      />
      <Input
        aria-label="模型"
        placeholder="模型"
        value={draftFilters.model_id ?? ""}
        onChange={(event) => onChange({ ...draftFilters, model_id: event.target.value })}
      />
      <Select
        value={draftFilters.event_type ?? "all"}
        onValueChange={(value) =>
          onChange({ ...draftFilters, event_type: value === null || value === "all" ? undefined : value })
        }
      >
        <SelectTrigger className="w-full" aria-label="事件类型">
          <SelectValue placeholder="事件类型" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部事件</SelectItem>
          {eventTypeOptions.map(([value, label]) => (
            <SelectItem key={value} value={value}>
              {label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select
        value={draftFilters.outcome ?? "all"}
        onValueChange={(value) =>
          onChange({ ...draftFilters, outcome: value === "all" ? undefined : (value as EventQueryOutcome) })
        }
      >
        <SelectTrigger className="w-full" aria-label="结果">
          <SelectValue placeholder="结果" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部结果</SelectItem>
          <SelectItem value="accepted">已受理</SelectItem>
          <SelectItem value="succeeded">成功</SelectItem>
          <SelectItem value="failed">失败</SelectItem>
          <SelectItem value="interrupted">已中断</SelectItem>
        </SelectContent>
      </Select>
      <Select
        value={draftFilters.health_outcome ?? "all"}
        onValueChange={(value) =>
          onChange({
            ...draftFilters,
            health_outcome: value === "all" ? undefined : (value as "succeeded" | "failed"),
          })
        }
      >
        <SelectTrigger className="w-full" aria-label="健康结果">
          <SelectValue placeholder="健康结果" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部健康结果</SelectItem>
          <SelectItem value="succeeded">健康成功</SelectItem>
          <SelectItem value="failed">健康失败</SelectItem>
        </SelectContent>
      </Select>
      <Select
        value={draftFilters.health_transition ?? "all"}
        onValueChange={(value) =>
          onChange({
            ...draftFilters,
            health_transition:
              value === "all" ? undefined : (value as NonNullable<EventLogFilters["health_transition"]>),
          })
        }
      >
        <SelectTrigger className="w-full" aria-label="健康状态">
          <SelectValue placeholder="健康状态" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部健康状态</SelectItem>
          {Object.entries(transitionLabels).map(([value, label]) => (
            <SelectItem key={value} value={value}>
              {label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Input
        aria-label="原因码"
        placeholder="原因码"
        value={draftFilters.reason_code ?? ""}
        onChange={(event) => onChange({ ...draftFilters, reason_code: event.target.value })}
      />
      <Input
        aria-label="Request ID"
        placeholder="Request ID"
        value={draftFilters.request_id ?? ""}
        onChange={(event) => onChange({ ...draftFilters, request_id: event.target.value })}
      />
      <Input
        aria-label="开始时间"
        type="datetime-local"
        value={draftFilters.occurred_after ?? ""}
        onChange={(event) => onChange({ ...draftFilters, occurred_after: event.target.value })}
      />
      <Input
        aria-label="结束时间"
        type="datetime-local"
        value={draftFilters.occurred_before ?? ""}
        onChange={(event) => onChange({ ...draftFilters, occurred_before: event.target.value })}
      />
      <div className="flex gap-2 sm:col-span-2 xl:col-span-4">
        <Button onClick={onApply} disabled={isFetching}>
          <Search />
          查询
        </Button>
        <Button variant="outline" onClick={onReset} disabled={isFetching}>
          <RotateCcw />
          重置
        </Button>
      </div>
    </section>
  );
}

interface EventTableProps {
  events: EventLogEntry[];
  isError: boolean;
  isFetching: boolean;
  isLoading: boolean;
  hasNextPage: boolean;
  hasPreviousPage: boolean;
  isPreviousPageEnabled: boolean;
  onNextPage: () => void;
  onPreviousPage: () => void;
  pageNumber: number;
  pageSize: number;
  onSelect: (event: EventLogEntry) => void;
}

export function EventTable({
  events,
  isError,
  isFetching,
  isLoading,
  hasNextPage,
  hasPreviousPage,
  isPreviousPageEnabled,
  onNextPage,
  onPreviousPage,
  pageNumber,
  pageSize,
  onSelect,
}: EventTableProps) {
  const action = isFetching ? <Loader2 className="size-4 animate-spin text-muted-foreground" /> : undefined;
  const showLoading = isLoading && events.length === 0;
  const showError = !showLoading && isError && events.length === 0;
  const showTable = !showLoading && !showError;
  return (
    <AccountPoolPanel title="事件与审计日志" description="按时间倒序展示脱敏后的健康、管理和运行事件" action={action}>
      {showLoading && <AccountPoolQueryState kind="loading" message="正在读取事件日志" className="min-h-40" />}
      {showError && (
        <AccountPoolQueryState kind="error" message="无法读取事件日志，请检查 PostgreSQL" className="min-h-40" />
      )}
      {showTable && (
        <Table className={accountPoolTableColumnDividerClass}>
          <TableHeader>
            <TableRow>
              <TableHead>时间</TableHead>
              <TableHead>事件</TableHead>
              <TableHead>范围</TableHead>
              <TableHead>结果</TableHead>
              <TableHead>原因</TableHead>
              <TableHead>关联信息</TableHead>
              <TableHead className="w-12">
                <span className="sr-only">详情</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {events.map((event) => (
              <TableRow key={event.event_id}>
                <TableCell className="whitespace-nowrap align-top text-xs">
                  {formatAccountPoolDateTime(event.occurred_at)}
                </TableCell>
                <TableCell className="align-top">
                  <strong className="block text-sm">{eventTypeLabels[event.event_type] ?? event.event_type}</strong>
                  <span className="mt-1 block text-xs text-muted-foreground">{event.event_type}</span>
                </TableCell>
                <TableCell className="max-w-56 break-all whitespace-normal align-top text-xs">
                  {eventScope(event)}
                </TableCell>
                <TableCell className="align-top">
                  <Badge
                    variant={
                      event.outcome === "failed" || event.outcome === "interrupted" ? "destructive" : "secondary"
                    }
                  >
                    {outcomeLabels[event.outcome]}
                  </Badge>
                </TableCell>
                <TableCell className="max-w-56 whitespace-normal align-top text-xs">
                  {event.reason_code ? reasonLabelWithCode(event.reason_code) : "无"}
                </TableCell>
                <TableCell className="max-w-64 whitespace-normal align-top text-xs">
                  {eventDetails(event)}
                  {event.request_id && (
                    <span className="mt-1 block break-all text-muted-foreground">请求 {event.request_id}</span>
                  )}
                </TableCell>
                <TableCell className="align-top">
                  <Button variant="ghost" size="icon-sm" title="查看事件详情" onClick={() => onSelect(event)}>
                    <Eye />
                    <span className="sr-only">查看事件详情</span>
                  </Button>
                </TableCell>
              </TableRow>
            ))}
            {!isLoading && events.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="h-28 text-center text-muted-foreground">
                  当前筛选条件下没有事件
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      )}
      {(hasPreviousPage || hasNextPage) && (
        <div className="flex items-center justify-between gap-3 border-t px-4 py-3">
          <span className="text-xs tabular-nums text-muted-foreground">
            第 {pageNumber} 页，每页 {pageSize} 条
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={onPreviousPage}
              disabled={!hasPreviousPage || !isPreviousPageEnabled}
            >
              <ChevronLeft />
              上一页
            </Button>
            <Button variant="outline" size="sm" onClick={onNextPage} disabled={!hasNextPage || isFetching}>
              {isFetching ? <Loader2 className="animate-spin" /> : <ChevronRight />}
              下一页
            </Button>
          </div>
        </div>
      )}
    </AccountPoolPanel>
  );
}

export function EventDetailsDialog({ event, onClose }: { event: EventLogEntry | null; onClose: () => void }) {
  return (
    <Dialog open={event !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>事件详情</DialogTitle>
          <DialogDescription>{event?.event_id}</DialogDescription>
        </DialogHeader>
        {event && (
          <div className="space-y-4 text-sm">
            <dl className="grid gap-3 sm:grid-cols-2">
              <EventDetail label="事件类型" value={eventTypeLabels[event.event_type] ?? event.event_type} />
              <EventDetail label="发生时间" value={formatAccountPoolDateTime(event.occurred_at)} />
              <EventDetail label="渠道 ID" value={event.channel_id ?? "无"} />
              <EventDetail label="模型" value={event.model_id ?? "无"} />
              <EventDetail label="Deployment" value={event.deployment_id ?? "无"} />
              <EventDetail label="Request ID" value={event.request_id ?? "无"} />
              <EventDetail label="Lease ID" value={event.lease_id ?? "无"} />
              <EventDetail label="操作者" value={`${event.actor_type} · ${event.actor_id}`} />
            </dl>
            <div>
              <h3 className="mb-2 text-xs font-medium text-muted-foreground">脱敏结构化详情</h3>
              <pre className="max-h-80 overflow-auto rounded-md bg-muted p-3 text-xs whitespace-pre-wrap break-all">
                {JSON.stringify(event.safe_details, null, 2)}
              </pre>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function EventDetail({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 break-all">{value}</dd>
    </div>
  );
}
