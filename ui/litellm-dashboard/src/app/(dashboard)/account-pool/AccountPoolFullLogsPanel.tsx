/** 本文件展示完整日志、会话顺序和独立清理入口，正文只在用户展开时加载。 */
import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Database, MessagesSquare, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { toast } from "@/lib/toast";
import {
  clearFullLogs,
  fullLogStorage,
  getFullLog,
  listFullLogs,
  type FullLogFilters,
  type FullLogSummary,
} from "./AccountPoolFullLogsApi";
import { requestInstructions, requestMessages, responseText } from "./AccountPoolConversation";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";

const number = (value: number | null | undefined) => (value == null ? "未知" : value.toLocaleString());

function logStatus(log: FullLogSummary): string {
  if (log.incomplete) return "未完成";
  return log.truncated ? "已截断" : "已保存";
}

export function FullLogContent({ accessToken, eventId }: { accessToken: string; eventId: string }) {
  const query = useQuery({
    queryKey: ["account-pool", "full-body", accessToken, eventId],
    queryFn: () => getFullLog(accessToken, eventId),
    gcTime: 0,
    retry: false,
  });
  if (query.isPending) return <p role="status">正在读取完整日志…</p>;
  if (query.isError) return <p role="alert">完整日志未保存、已清理或暂时不可用</p>;
  const log = query.data;
  const instructions = requestInstructions(log.request);
  return (
    <div className="grid min-w-0 gap-4">
      {(log.incomplete || log.truncated) && (
        <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
          {log.incomplete ? "回复未完成，以下为已收到的内容。" : ""}
          {log.truncated ? "内容超过保存上限，记录已截断。" : ""}
        </p>
      )}
      {requestMessages(log.request).map((message, index) => (
        <article key={index} className="rounded-xl border bg-muted/30 p-4">
          <p className="mb-2 text-xs font-medium text-muted-foreground">
            {message.role === "user" ? "用户输入" : message.role}
          </p>
          <pre className="whitespace-pre-wrap break-words font-sans text-sm">{message.content}</pre>
        </article>
      ))}
      <article className="rounded-xl border border-primary/20 bg-primary/5 p-4">
        <p className="mb-2 text-xs font-medium text-muted-foreground">模型回复 · {log.model}</p>
        <pre className="whitespace-pre-wrap break-words font-sans text-sm">{responseText(log.response)}</pre>
      </article>
      {instructions && (
        <details className="rounded-lg border p-3">
          <summary className="cursor-pointer text-sm">提示词</summary>
          <pre className="mt-3 whitespace-pre-wrap break-words text-xs">{instructions}</pre>
        </details>
      )}
      <dl className="grid grid-cols-2 gap-3 rounded-lg border p-3 text-xs sm:grid-cols-4">
        {[
          ["输入", log.result.input_tokens],
          ["输出", log.result.output_tokens],
          ["缓存读取", log.result.cache_read_input_tokens],
          ["缓存写入", log.result.cache_creation_input_tokens],
        ].map(([name, value]) => (
          <div key={String(name)}>
            <dt className="text-muted-foreground">{name}</dt>
            <dd className="mt-1 font-mono">{number(value as number | null | undefined)}</dd>
          </div>
        ))}
      </dl>
      <p className="break-all text-xs text-muted-foreground">
        代理：{log.result.proxy_endpoint ?? "未知"} · 估算成本：
        {log.result.cost_usd == null ? "价格或用量未知" : `$${log.result.cost_usd.toFixed(8)}`} · 会话：
        {log.session_id ?? "未关联"}
      </p>
      <details className="min-w-0 rounded-lg border p-3">
        <summary className="cursor-pointer text-sm">原始完整记录（包含历史上下文、工具调用和计价依据）</summary>
        <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-all text-xs">
          {JSON.stringify(log, null, 2)}
        </pre>
      </details>
    </div>
  );
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
          <DialogDescription>本轮输入与回复，原始上下文保存在完整记录中</DialogDescription>
        </DialogHeader>
        {eventId && <FullLogContent accessToken={accessToken} eventId={eventId} />}
      </DialogContent>
    </Dialog>
  );
}

export function AccountPoolFullLogsPanel({
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
  const [days, setDays] = useState("30");
  const [busy, setBusy] = useState(false);
  const [eventId, setEventId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["account-pool", "full-logs", accessToken, filters],
    queryFn: () => listFullLogs(accessToken, filters),
    retry: false,
  });
  const storage = useQuery({
    queryKey: ["account-pool", "full-log-storage", accessToken],
    queryFn: () => fullLogStorage(accessToken),
    retry: false,
  });
  const searchSession = (event: FormEvent) => {
    event.preventDefault();
    setFilters({ ...filters, session_id: session.trim() || undefined, offset: 0 });
    setExpanded(null);
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
            <h3 className="text-sm font-medium">完整日志独立存储</h3>
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
      <p className="text-sm text-muted-foreground">
        在“设置 / 日志”开启完整记录。只有开启后的请求会保存正文，关闭不会删除已有记录。
      </p>
      <form className="flex flex-wrap gap-2" onSubmit={searchSession}>
        <Input
          className="min-w-0 flex-1 basis-48"
          aria-label="会话 ID"
          placeholder="输入会话 ID，按时间查看整段对话"
          value={session}
          onChange={(event) => setSession(event.target.value)}
        />
        <Button type="submit" variant="outline">
          查询会话
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
          暂无完整日志；开启后发起新请求即可记录
        </div>
      )}
      {query.data?.items.map((log) => (
        <article key={log.event_id} className="min-w-0 rounded-xl border p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
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
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setSession(log.session_id!);
                    setFilters({ ...filters, session_id: log.session_id!, offset: 0 });
                  }}
                >
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
      <div className="flex gap-2">
        <Button
          variant="outline"
          disabled={!filters.offset}
          onClick={() => setFilters({ ...filters, offset: Math.max(0, (filters.offset ?? 0) - 50) })}
        >
          上一页
        </Button>
        <Button
          variant="outline"
          disabled={!query.data?.has_more}
          onClick={() => setFilters({ ...filters, offset: (filters.offset ?? 0) + 50 })}
        >
          下一页
        </Button>
      </div>
      <FullLogDialog accessToken={accessToken} eventId={eventId} onClose={() => setEventId(null)} />
    </div>
  );
}
