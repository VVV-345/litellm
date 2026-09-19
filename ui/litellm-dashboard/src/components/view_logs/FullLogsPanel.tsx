/** 本文件展示完整日志、会话顺序和独立清理入口，正文只在用户展开时加载。 */
import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Database, MessagesSquare, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { toast } from "@/lib/toast";
import { DataTablePagination } from "@/components/shared/DataTable/DataTablePagination";
import { ConversationRecordView, ConversationExport } from "./ConversationRecordView";
import {
  clearFullLogs,
  fullLogStorage,
  getFullLog,
  listFullLogs,
  type FullLogFilters,
  type FullLogSummary,
} from "./fullLogsApi";
import type { AccountPoolEnvironment } from "@/app/(dashboard)/account-pool/AccountPoolTypes";

const number = (value: number | null | undefined) => (value == null ? "未知" : value.toLocaleString());

function logStatus(log: FullLogSummary): string {
  if (log.incomplete) return "未完成";
  return log.truncated ? "已截断" : "已保存";
}

export function FullLogContent({ accessToken, eventId }: { accessToken: string; eventId: string }) {
  const query = useQuery({
    queryKey: ["logs", "full-body", accessToken, eventId],
    queryFn: () => getFullLog(accessToken, eventId),
    gcTime: 0,
    retry: false,
  });
  if (query.isPending) return <p role="status">正在读取完整日志…</p>;
  if (query.isError) return <p role="alert">完整日志未保存、已清理或暂时不可用</p>;
  const log = query.data;
  return <ConversationRecordView log={log} />;
}

export function FullLogDialog({
  accessToken,
  eventId,
  onClose,
}: {
  accessToken: string;
  eventId: string | null;
  onClose: () => void;
}) {
  return (
    <Dialog
      open={eventId !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>完整日志</DialogTitle>
          <DialogDescription>按角色查看输入、思考与工具调用，支持完整上下文和导出</DialogDescription>
        </DialogHeader>
        {eventId && <FullLogContent accessToken={accessToken} eventId={eventId} />}
      </DialogContent>
    </Dialog>
  );
}

export function FullLogsPanel({
  accessToken,
  environments,
  initialCardId,
}: {
  accessToken: string;
  environments: AccountPoolEnvironment[];
  initialCardId?: string;
}) {
  const [filters, setFilters] = useState<FullLogFilters>({ card_id: initialCardId });
  const [session, setSession] = useState("");
  const [model, setModel] = useState("");
  const [requestId, setRequestId] = useState("");
  const [resultFilter, setResultFilter] = useState("all");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [days, setDays] = useState("30");
  const [busy, setBusy] = useState(false);
  const [eventId, setEventId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const pageSize = filters.limit ?? 50;
  const offset = filters.offset ?? 0;
  const resetFilters = () => {
    setFilters({ card_id: initialCardId });
    setSession("");
    setModel("");
    setRequestId("");
    setResultFilter("all");
    setFrom("");
    setTo("");
    setExpanded(null);
  };
  const query = useQuery({
    queryKey: ["logs", "full-logs", accessToken, filters],
    queryFn: () => listFullLogs(accessToken, filters),
    retry: false,
  });
  const storage = useQuery({
    queryKey: ["logs", "full-log-storage", accessToken],
    queryFn: () => fullLogStorage(accessToken),
    retry: false,
  });
  const searchSession = (event: FormEvent) => {
    event.preventDefault();
    if (
      (from && !Number.isFinite(Date.parse(from))) ||
      (to && !Number.isFinite(Date.parse(to))) ||
      (from && to && Date.parse(from) > Date.parse(to))
    ) {
      toast.error("请检查开始和结束时间");
      return;
    }
    setFilters({
      ...filters,
      key_id: session.trim() === filters.session_id ? filters.key_id : undefined,
      session_id: session.trim() || undefined,
      model: model.trim() || undefined,
      request_id: requestId.trim() || undefined,
      incomplete: resultFilter === "all" ? undefined : resultFilter === "failed",
      occurred_from: from ? new Date(from).toISOString() : undefined,
      occurred_to: to ? new Date(to).toISOString() : undefined,
      offset: 0,
    });
    setExpanded(null);
  };
  const openSession = (log: FullLogSummary) => {
    if (!log.session_id) return;
    setSession(log.session_id);
    setModel("");
    setRequestId("");
    setResultFilter("all");
    setFrom("");
    setTo("");
    setFilters({ card_id: initialCardId, key_id: log.key_id, session_id: log.session_id, offset: 0, limit: pageSize });
    setExpanded(log.event_id);
  };
  const clear = async () => {
    if (
      !window.confirm(
        days === "all" ? "清理全部完整日志？日常日志不受影响。" : `清理 ${days} 天以前的完整日志？日常日志不受影响。`,
      )
    )
      return;
    setBusy(true);
    try {
      const result = await clearFullLogs(accessToken, days === "all" ? null : Number(days));
      toast.success(`已清理 ${result.deleted} 条完整日志`);
      setExpanded(null);
      setEventId(null);
      setFilters({ ...filters, offset: 0 });
      await Promise.all([query.refetch(), storage.refetch()]);
    } catch {
      toast.error("完整日志清理失败，请重试");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="grid gap-4">
      <div className="flex flex-col gap-4 rounded-xl border bg-muted/20 p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 gap-3">
          <Database className="mt-1 size-5 shrink-0" />
          <div className="min-w-0">
            <h3 className="text-sm font-medium">完整日志存储</h3>
            <p className="mt-1 break-all text-xs text-muted-foreground">
              {storage.data?.location ?? "正在读取存储信息"}
            </p>
            {storage.data && (
              <p className="mt-1 text-xs text-muted-foreground">
                {storage.data.row_count} 条 · {(storage.data.allocated_bytes / 1024 / 1024).toFixed(2)} MiB
              </p>
            )}
            {storage.isError && <p role="alert">存储信息读取失败</p>}
          </div>
        </div>
        <div className="flex shrink-0 gap-2">
          <select
            aria-label="完整日志清理范围"
            className="rounded-md border bg-background px-2 text-sm"
            value={days}
            onChange={(event) => setDays(event.target.value)}
          >
            {[7, 14, 30, 45].map((value) => (
              <option key={value} value={value}>
                {value} 天以前
              </option>
            ))}
            <option value="all">全部完整日志</option>
          </select>
          <Button variant="outline" disabled={busy} onClick={() => void clear()}>
            <Trash2 className="mr-2 size-4" />
            清理
          </Button>
        </div>
      </div>
      <p className="text-sm text-muted-foreground">在“日志设置”开启完整记录后，新请求会保存正文；已有记录继续可查</p>
      <form className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" onSubmit={searchSession}>
        <Input
          aria-label="完整日志模型"
          placeholder="模型"
          value={model}
          onChange={(event) => setModel(event.target.value)}
        />
        <Input
          aria-label="完整日志请求 ID"
          placeholder="请求 ID"
          value={requestId}
          onChange={(event) => setRequestId(event.target.value)}
        />
        <select
          aria-label="完整日志结果"
          value={resultFilter}
          onChange={(event) => setResultFilter(event.target.value)}
          className="rounded-md border bg-background px-2 text-sm"
        >
          <option value="all">全部结果</option>
          <option value="success">成功</option>
          <option value="failed">失败或中断</option>
        </select>
        <Input
          type="datetime-local"
          aria-label="完整日志开始时间"
          value={from}
          onChange={(event) => setFrom(event.target.value)}
        />
        <Input
          type="datetime-local"
          aria-label="完整日志结束时间"
          value={to}
          onChange={(event) => setTo(event.target.value)}
        />
        <Input
          className="min-w-0 flex-1 basis-48"
          aria-label="会话 ID"
          placeholder="输入会话 ID，按时间查看整段对话"
          value={session}
          onChange={(event) => setSession(event.target.value)}
        />
        <Button type="submit" variant="outline">
          查询日志
        </Button>
        <Button type="button" variant="ghost" onClick={resetFilters}>
          重置筛选
        </Button>
        {filters.session_id && (
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              setSession("");
              setFilters({ card_id: initialCardId });
            }}
          >
            返回列表
          </Button>
        )}
      </form>
      {filters.session_id && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-primary/5 p-4">
          <div className="min-w-0">
            <h3 className="font-medium">会话时间线</h3>
            <p className="mt-1 break-all text-xs text-muted-foreground">{filters.session_id}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              按请求发生时间排列；每轮可切换本轮内容与完整上下文，重试单独标记
            </p>
          </div>
          <ConversationExport accessToken={accessToken} filters={filters} />
        </div>
      )}
      {query.data?.totals && (
        <div className="grid grid-cols-2 gap-3 rounded-xl border p-4 text-sm sm:grid-cols-4">
          <div>
            <p className="text-xs text-muted-foreground">当前筛选记录</p>
            <p className="mt-1">
              {number(query.data.totals.requests)} 个请求 / {number(query.data.totals.attempts)} 次尝试
            </p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">输入 / 输出</p>
            <p className="mt-1">
              {number(query.data.totals.input_tokens)} / {number(query.data.totals.output_tokens)}
            </p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">缓存读取 / 写入</p>
            <p className="mt-1">
              {number(query.data.totals.cache_read_input_tokens)} /{" "}
              {number(query.data.totals.cache_creation_input_tokens)}
            </p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">已知估算成本</p>
            <p className="mt-1">
              {query.data.totals.cost_usd == null ? "未知" : `$${query.data.totals.cost_usd.toFixed(6)}`}
            </p>
            {!!query.data.totals.unknown_cost_attempts && (
              <p className="mt-1 text-xs text-muted-foreground">
                另有 {query.data.totals.unknown_cost_attempts} 次价格或用量未知
              </p>
            )}
          </div>
        </div>
      )}
      {query.isPending && <p role="status">正在读取完整日志…</p>}
      {query.isError && (
        <p role="alert">
          完整日志加载失败
          <Button variant="link" onClick={() => void query.refetch()}>
            重试
          </Button>
        </p>
      )}
      {query.data?.items.length === 0 && (
        <div className="rounded-xl border border-dashed p-10 text-center text-sm text-muted-foreground">
          <MessagesSquare className="mx-auto mb-3 size-8" />
          当前筛选下暂无完整日志；可调整筛选，或在日志设置开启后记录新请求
        </div>
      )}
      {query.data?.items.map((log, index) => (
        <article key={log.event_id} className="min-w-0 rounded-xl border p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              {filters.session_id && <p className="mb-1 text-xs text-primary">记录 {offset + index + 1}</p>}
              <p className="break-all text-sm font-medium">
                {environments.find((item) => item.id === log.card_id)?.name ?? log.card_id} · {log.model}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {new Date(log.started_at).toLocaleString()} · 第 {log.attempt} 次尝试 · 输入{" "}
                {number(log.result.input_tokens)} / 输出 {number(log.result.output_tokens)}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={log.incomplete ? "destructive" : "secondary"}>{logStatus(log)}</Badge>
              {filters.session_id ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setExpanded(expanded === log.event_id ? null : log.event_id)}
                >
                  展开本轮
                </Button>
              ) : (
                <Button variant="outline" size="sm" onClick={() => setEventId(log.event_id)}>
                  查看完整日志
                </Button>
              )}
              {log.session_id && !filters.session_id && (
                <Button variant="ghost" size="sm" onClick={() => openSession(log)}>
                  查看会话
                </Button>
              )}
            </div>
          </div>
          {expanded === log.event_id && (
            <div className="mt-4 border-t pt-4">
              <FullLogContent accessToken={accessToken} eventId={log.event_id} />
            </div>
          )}
        </article>
      ))}
      {query.data && (
        <DataTablePagination
          page={Math.floor(offset / pageSize)}
          pageSize={pageSize}
          rowCount={query.data.totals?.attempts ?? 0}
          showPageJump
          isLoading={query.isFetching}
          onPageChange={(page) => {
            setFilters({ ...filters, offset: page * pageSize });
            setExpanded(null);
          }}
          onPageSizeChange={(limit) => {
            setFilters({ ...filters, limit, offset: 0 });
            setExpanded(null);
          }}
        />
      )}
      <FullLogDialog accessToken={accessToken} eventId={eventId} onClose={() => setEventId(null)} />
    </div>
  );
}
