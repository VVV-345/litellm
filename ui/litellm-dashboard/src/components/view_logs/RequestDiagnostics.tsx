import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { listOperationLogs, getOperationLog } from "./operationLogsApi";
import { listFullLogs } from "./fullLogsApi";
import { FullLogContent } from "./FullLogsPanel";
import { apiClient } from "@/components/networking";
import type { components } from "@/lib/http/schema";

const phaseLabels: Record<string, string> = {
  resolve: "解析账号权限",
  acquire: "检查可用状态并占用卡片",
  upstream_headers: "等待账号环境响应头（含代理与上游）",
  upstream_and_delivery: "上游处理与返回内容（包含等待响应头）",
  persist_and_release: "保存记录并释放卡片",
  ingress_total: "网关收到请求至完成返回",
};

export function RequestDiagnostics({ accessToken, requestId }: { accessToken: string; requestId: string }) {
  const [expanded, setExpanded] = useState(false);
  const timing = useQuery({
    queryKey: ["logs", "request-timing", accessToken, requestId],
    queryFn: () =>
      apiClient.get<components["schemas"]["TimingPhase"][]>(`/logs/timing/${encodeURIComponent(requestId)}`, {
        accessToken,
      }),
    retry: false,
  });
  const events = useQuery({
    queryKey: ["logs", "request-operations", accessToken, requestId],
    queryFn: () => listOperationLogs(accessToken, { request_id: requestId, limit: 1 }),
    retry: false,
  });
  const eventId = events.data?.items[0]?.event_id;
  const detail = useQuery({
    queryKey: ["logs", "request-attempts", accessToken, eventId],
    queryFn: () => getOperationLog(accessToken, eventId!),
    enabled: !!eventId,
    retry: false,
  });
  const full = useQuery({
    queryKey: ["logs", "request-full", accessToken, requestId],
    queryFn: () => listFullLogs(accessToken, { request_id: requestId }),
    retry: false,
  });
  return (
    <section className="mb-6 grid gap-3 rounded-lg border p-4">
      <h3 className="font-semibold">账号调度与完整记录</h3>
      {!!timing.data?.length && (
        <div className="space-y-2 rounded-lg border p-3">
          <h4 className="text-sm font-medium">请求阶段耗时</h4>
          <dl className="grid gap-2 sm:grid-cols-2">
            {timing.data.map((item) => (
              <div key={`${item.segment_id}-${item.phase}`} className="rounded-md bg-muted/30 p-2 text-xs">
                <dt className="text-muted-foreground">
                  {phaseLabels[item.phase] ?? item.phase}
                  {item.attempt > 0 ? ` · 尝试 ${item.attempt}` : ""}
                </dt>
                <dd className="mt-1 font-mono">
                  {item.duration_ms.toFixed(0)} ms · HTTP {item.status}
                </dd>
              </div>
            ))}
          </dl>
          <p className="text-xs text-muted-foreground">
            阶段包含重叠时间，不能直接相加。代理与供应商内部耗时尚无法完全拆开。记录保留 7 天，最多 10 万条阶段记录。
          </p>
        </div>
      )}
      {timing.isError && (
        <p role="alert" className="text-xs text-destructive">
          阶段耗时读取失败
        </p>
      )}
      {timing.data?.length === 0 && (
        <p className="text-xs text-muted-foreground">此请求没有阶段耗时记录，可能产生于升级前或已过保留期</p>
      )}
      {(events.isPending || full.isPending) && <p role="status">正在读取请求记录…</p>}
      {(events.isError || detail.isError || full.isError) && <p role="alert">部分诊断记录读取失败</p>}
      {detail.data?.attempts.map((attempt) => (
        <div key={attempt.event_id} className="rounded-md bg-muted/30 p-3 text-sm">
          <p>
            尝试 {attempt.attempt} · 账号 {attempt.account_id} · HTTP {attempt.http_status ?? "未知"}
          </p>
          <p>{attempt.message}</p>
          <p className="text-muted-foreground">
            {attempt.duration_ms ?? "未知"} ms · {attempt.upstream_code ?? attempt.routing_reason ?? ""}
          </p>
        </div>
      ))}
      {full.data && full.data.items.length === 0 && (
        <p className="text-sm text-muted-foreground">完整正文未保存或已清理；可在日志设置开启后记录新请求</p>
      )}
      {!!full.data?.items.length && (
        <Button variant="outline" onClick={() => setExpanded(!expanded)}>
          {expanded ? "收起完整日志" : "查看完整日志"}
        </Button>
      )}
      {expanded &&
        full.data?.items.map((record) => (
          <FullLogContent key={record.event_id} accessToken={accessToken} eventId={record.event_id} />
        ))}
    </section>
  );
}
