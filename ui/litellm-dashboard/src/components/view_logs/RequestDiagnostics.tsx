import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { listOperationLogs, getOperationLog } from "./operationLogsApi";
import { listFullLogs } from "./fullLogsApi";
import { FullLogContent } from "./FullLogsPanel";

export function RequestDiagnostics({ accessToken, requestId }: { accessToken: string; requestId: string }) {
  const [expanded, setExpanded] = useState(false);
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
