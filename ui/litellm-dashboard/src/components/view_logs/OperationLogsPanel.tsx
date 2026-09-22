/** 本文件展示日常日志和完整日志入口，日常列表只读取摘要。 */

import { FullLogDialog } from "./FullLogsPanel";
import { DataTablePagination } from "@/components/shared/DataTable/DataTablePagination";
import { Database, Download, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { formatDateTime } from "@/features/account-pool/utils/AccountPoolFormatters";
import {
  clearOperationLogs,
  exportOperationLogs,
  getOperationLog,
  getOperationLogStorage,
  getOperationStats,
  operationLogsQueryOptions,
  type LogDetail,
  type LogFilters,
} from "./operationLogsApi";
import { toast } from "@/lib/toast";
import type { AccountPoolEnvironment } from "@/features/account-pool/utils/AccountPoolTypes";
import { getLiveTailRefetchInterval } from "./log_filter_logic";
import { LogAutoRefresh } from "./LogAutoRefresh";

const COST_FORMAT_OPTIONS: Intl.NumberFormatOptions = {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 10,
};

const formatCost = (value: number | null | undefined) =>
  value == null ? null : new Intl.NumberFormat("en-US", COST_FORMAT_OPTIONS).format(value);
const formatBytes = (value: number) => {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MiB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GiB`;
};

type LogEvent = LogDetail["event"];
type DetailField =
  | "card_id"
  | "environment_id"
  | "account_id"
  | "card_key_id"
  | "request_id"
  | "upstream_request_id"
  | "trace_id"
  | "model"
  | "endpoint"
  | "method"
  | "http_status"
  | "upstream_code"
  | "duration_ms"
  | "input_tokens"
  | "output_tokens"
  | "cache_read_input_tokens"
  | "cache_creation_input_tokens"
  | "routing_reason"
  | "cost_usd";

const detailFieldValue = (
  event: LogEvent,
  field: DetailField,
  unavailable: string,
  routeReason: (reason: NonNullable<LogEvent["routing_reason"]>) => string,
) => {
  if (field === "routing_reason") return event.routing_reason ? routeReason(event.routing_reason) : unavailable;
  if (field === "cost_usd") return formatCost(event.cost_usd) ?? unavailable;
  return event[field] ?? unavailable;
};

export function OperationLogsPanel({
  accessToken,
  environments,
  initialCardId,
}: {
  accessToken: string;
  environments: AccountPoolEnvironment[];
  initialCardId?: string;
}) {
  const { t, i18n } = useTranslation();
  const [draft, setDraft] = useState<LogFilters>({ card_id: initialCardId });
  const [filters, setFilters] = useState<LogFilters>({ card_id: initialCardId });
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [offset, setOffset] = useState(0);
  const [pageSize, setPageSize] = useState(50);
  const [eventId, setEventId] = useState<string | null>(null);
  const [fullEventId, setFullEventId] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const refreshPaused = offset > 0 || eventId !== null || fullEventId !== null || Boolean(filters.occurred_to);
  const refreshInterval = getLiveTailRefetchInterval(autoRefresh && !refreshPaused, 0);
  const [validationError, setValidationError] = useState(false);
  const [retentionDays, setRetentionDays] = useState<"all" | "7" | "14" | "30" | "45">("30");
  const resetFilters = () => {
    setDraft({ card_id: initialCardId });
    setFilters({ card_id: initialCardId });
    setFrom("");
    setTo("");
    setOffset(0);
    setValidationError(false);
  };
  const pageQuery = { ...filters, offset, limit: pageSize };
  const query = useQuery(operationLogsQueryOptions({ accessToken, filters, offset, pageSize, refreshInterval }));
  const detailQuery = {
    queryKey: ["logs", "log-detail", accessToken, eventId],
    queryFn: () => getOperationLog(accessToken, eventId!),
    enabled: eventId !== null,
    retry: false,
  };
  const detail = useQuery(detailQuery);
  const stats = useQuery({
    queryKey: ["logs", "stats", accessToken, filters],
    queryFn: () => getOperationStats(accessToken, filters),
    retry: false,
    refetchInterval: refreshInterval === false ? false : 30_000,
    refetchIntervalInBackground: false,
  });
  const storage = useQuery({
    queryKey: ["logs", "log-storage", accessToken],
    queryFn: () => getOperationLogStorage(accessToken),
    retry: false,
    refetchInterval: refreshInterval === false ? false : 60_000,
    refetchIntervalInBackground: false,
  });
  const submitFilters = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const invalidFrom = Boolean(from) && !Number.isFinite(Date.parse(from));
    const invalidTo = Boolean(to) && !Number.isFinite(Date.parse(to));
    const reversed = Boolean(from && to) && Date.parse(from) > Date.parse(to);
    const invalidRange = invalidFrom || invalidTo || reversed;
    if (invalidRange) {
      setValidationError(true);
      return;
    }
    setValidationError(false);
    setFilters({
      ...draft,
      occurred_from: from ? new Date(from).toISOString() : undefined,
      occurred_to: to ? new Date(to).toISOString() : undefined,
    });
    setOffset(0);
  };
  const downloadLogs = async () => {
    try {
      const blob = await exportOperationLogs(accessToken, pageQuery);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "operation-logs.ndjson";
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast.fromError(error);
    }
  };
  const clearLogs = async () => {
    if (!window.confirm(t("accountPool.logs.clearConfirm"))) return;
    try {
      const result = await clearOperationLogs(
        accessToken,
        retentionDays === "all" ? undefined : (Number(retentionDays) as 7 | 14 | 30 | 45),
      );
      toast.success(t("accountPool.logs.cleared", { count: result.deleted }));
      setOffset(0);
      await Promise.all([query.refetch(), stats.refetch(), storage.refetch()]);
    } catch (error) {
      toast.fromError(error);
    }
  };
  const choice = (
    field: "channel" | "supplier" | "card_id" | "account_id" | "stage" | "error_category" | "final_status" | "endpoint",
    options: { value: string; label: string }[],
    label = t(`accountPool.logs.${field}`),
  ) => (
    <div className="grid gap-1" key={field}>
      <Label>{label}</Label>
      <Select
        items={[{ value: "all", label: t("accountPool.management.all") }, ...options]}
        value={draft[field] || "all"}
        onValueChange={(value) => setDraft((current) => ({ ...current, [field]: value === "all" ? undefined : value }))}
      >
        <SelectTrigger className="w-full" aria-label={label}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">{t("accountPool.management.all")}</SelectItem>
          {options.map((item) => (
            <SelectItem value={item.value} key={item.value}>
              {item.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
  return (
    <div className="grid gap-4">
      <p className="text-sm text-muted-foreground">{t("accountPool.logs.description")}</p>
      <LogAutoRefresh enabled={autoRefresh} paused={refreshPaused} onChange={setAutoRefresh} />
      {storage.data && (
        <div className="flex flex-col gap-3 rounded-md border bg-muted/20 p-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-center gap-3">
            <Database className="size-5 shrink-0 text-muted-foreground" />
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{storage.data.location}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {t("accountPool.logs.storageUsage", {
                  size: formatBytes(storage.data.allocated_bytes),
                  count: storage.data.row_count,
                })}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Select value={retentionDays} onValueChange={(value) => setRetentionDays(value as typeof retentionDays)}>
              <SelectTrigger className="w-44" aria-label={t("accountPool.logs.clearRange")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(["7", "14", "30", "45"] as const).map((days) => (
                  <SelectItem key={days} value={days}>
                    {t("accountPool.logs.olderThanDays", { days })}
                  </SelectItem>
                ))}
                <SelectItem value="all">{t("accountPool.logs.allLogs")}</SelectItem>
              </SelectContent>
            </Select>
            <Button type="button" variant="destructive" onClick={() => void clearLogs()}>
              <Trash2 />
              {t("accountPool.logs.clearSelected")}
            </Button>
          </div>
        </div>
      )}
      <form className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" onSubmit={submitFilters}>
        {choice(
          "channel",
          ["cliproxyapi"].map((value) => ({ value, label: t(`accountPool.channel.${value}`) })),
        )}
        {choice(
          "supplier",
          ["openai_codex", "anthropic_claude", "google_antigravity", "kimi", "xai"].map((value) => ({
            value,
            label: t(`accountPool.supplier.${value}`),
          })),
        )}
        {choice(
          "stage",
          [
            "provisioning",
            "authorization",
            "validation",
            "configuration",
            "quota",
            "cleanup",
            "authentication",
            "routing",
            "connection",
            "upstream",
            "response",
            "card_key",
          ].map((value) => ({ value, label: t(`accountPool.logs.stages.${value}`) })),
        )}
        {choice(
          "error_category",
          [
            "authentication",
            "authorization",
            "rate_limit",
            "timeout",
            "connection",
            "invalid_request",
            "upstream",
            "configuration",
            "unknown",
          ].map((value) => ({ value, label: t(`accountPool.logs.categories.${value}`) })),
        )}
        {choice(
          "account_id",
          environments.map((card) => ({ value: card.id, label: card.name })),
          "实际使用卡片",
        )}
        {choice(
          "final_status",
          [
            { value: "succeeded", label: "成功" },
            { value: "failed", label: "失败" },
            { value: "retrying", label: "重试中" },
          ],
          "请求结果",
        )}
        {choice(
          "endpoint",
          [
            "/v1/chat/completions",
            "/v1/responses",
            "/v1/responses/compact",
            "/v1/images/generations",
            "/v1/realtime",
          ].map((value) => ({ value, label: value })),
          "请求端点",
        )}
        <div className="grid gap-1">
          <Label htmlFor="log-http-status">HTTP 状态码</Label>
          <Input
            id="log-http-status"
            type="number"
            min={100}
            max={599}
            value={draft.http_status ?? ""}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                http_status: event.target.value ? Number(event.target.value) : undefined,
              }))
            }
          />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="log-session-id">会话 ID</Label>
          <Input
            id="log-session-id"
            value={draft.session_id ?? ""}
            onChange={(event) => setDraft((current) => ({ ...current, session_id: event.target.value || undefined }))}
          />
        </div>
        {(["card_key_id", "request_id", "model"] as const).map((field) => (
          <div className="grid gap-1" key={field}>
            <Label htmlFor={`log-filter-${field}`}>{t(`accountPool.logs.${field}`)}</Label>
            <Input
              id={`log-filter-${field}`}
              value={draft[field] ?? ""}
              onChange={(event) => setDraft((current) => ({ ...current, [field]: event.target.value || undefined }))}
            />
          </div>
        ))}
        {(["retryable", "switched_account"] as const).map((field) => (
          <div className="grid gap-1" key={field}>
            <Label>{t(`accountPool.logs.${field}`)}</Label>
            <Select
              items={["all", "true", "false"].map((value) => ({ value, label: t(`accountPool.management.${value}`) }))}
              value={draft[field] === undefined ? "all" : String(draft[field])}
              onValueChange={(value) =>
                setDraft((current) => ({ ...current, [field]: value === "all" ? undefined : value === "true" }))
              }
            >
              <SelectTrigger className="w-full" aria-label={t(`accountPool.logs.${field}`)}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {["all", "true", "false"].map((value) => (
                  <SelectItem key={value} value={value}>
                    {t(`accountPool.management.${value}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ))}
        <div className="grid gap-1">
          <Label htmlFor="log-from">{t("accountPool.logs.from")}</Label>
          <Input id="log-from" type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="log-to">{t("accountPool.logs.to")}</Label>
          <Input id="log-to" type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} />
        </div>
        <div className="flex items-end gap-2">
          <Button type="submit">{t("accountPool.logs.apply")}</Button>
          <Button type="button" variant="ghost" onClick={resetFilters}>
            重置筛选
          </Button>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            {t("ui.Refresh")}
          </Button>
          <Button type="button" variant="outline" onClick={() => void downloadLogs()}>
            <Download />
            {t("accountPool.logs.export")}
          </Button>
        </div>
      </form>
      {validationError && <p role="alert">{t("accountPool.logs.invalidTime")}</p>}
      {stats.data && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
          <div className="rounded border p-3">
            <p className="text-xs text-muted-foreground">{t("accountPool.stats.requests")}</p>
            <p className="text-xl font-semibold">{stats.data.total_requests}</p>
          </div>
          <div className="rounded border p-3">
            <p className="text-xs text-muted-foreground">{t("accountPool.stats.failures")}</p>
            <p className="text-xl font-semibold">{stats.data.failed_requests}</p>
          </div>
          <div className="rounded border p-3">
            <p className="text-xs text-muted-foreground">{t("accountPool.stats.tokens")}</p>
            <p className="text-xl font-semibold">{stats.data.input_tokens + stats.data.output_tokens}</p>
          </div>
          <div className="rounded border p-3">
            <p className="text-xs text-muted-foreground">{t("accountPool.stats.cacheRate")}</p>
            <p className="text-xl font-semibold">
              {stats.data.cache_rate == null ? "-" : `${(stats.data.cache_rate * 100).toFixed(1)}%`}
            </p>
            <p className="text-xs text-muted-foreground">
              {t("accountPool.stats.cacheTokens", { count: stats.data.cache_read_input_tokens })}
            </p>
          </div>
          <div className="rounded border p-3">
            <p className="text-xs text-muted-foreground">{t("accountPool.stats.averageDuration")}</p>
            <p className="text-xl font-semibold">
              {stats.data.average_duration_ms == null ? "-" : Math.round(stats.data.average_duration_ms)} ms
            </p>
          </div>
          <div className="rounded border p-3">
            <p className="text-xs text-muted-foreground">{t("accountPool.stats.cost")}</p>
            <p className="text-xl font-semibold">
              {formatCost(stats.data.total_cost_usd) ?? t("accountPool.stats.costUnavailable")}
            </p>
            <p className="text-xs text-muted-foreground">
              {t("accountPool.stats.costCoverage", {
                known: stats.data.known_cost_requests,
                total: stats.data.total_requests,
              })}
            </p>
          </div>
        </div>
      )}
      {query.isPending && <p role="status">{t("accountPool.management.loading")}</p>}
      {query.isError && <p role="alert">{t("accountPool.logs.loadFailed")}</p>}
      {query.data && (
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-left text-xs">
            <caption className="px-3 py-2 text-left text-xs text-muted-foreground">
              {t("accountPool.logs.usageHint")}
            </caption>
            <thead className="bg-muted/60 text-muted-foreground">
              <tr>
                {[
                  "time",
                  "card_id",
                  "model",
                  "input_tokens",
                  "output_tokens",
                  "cache_read_input_tokens",
                  "cost_usd",
                  "cache_rate",
                  "duration_ms",
                  "result",
                  "message",
                ].map((field) => (
                  <th
                    key={field}
                    className={`whitespace-nowrap px-3 py-3 font-medium ${field.endsWith("tokens") || field === "duration_ms" ? "text-right" : ""}`}
                    scope="col"
                  >
                    {field === "cost_usd" ? "费用（USD）" : t(`accountPool.logs.${field}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {query.data.items.map((event) => (
                <tr
                  key={event.event_id}
                  className={`border-t align-top transition-colors hover:bg-muted/40 ${event.final_status === "failed" ? "bg-destructive/5" : ""}`}
                >
                  <td className="whitespace-nowrap px-3 py-3 tabular-nums">
                    {formatDateTime(event.occurred_at, i18n.language)}
                  </td>
                  <td className="min-w-28 max-w-48 px-3 py-3">
                    <p className="break-words font-medium">
                      {environments.find((item) => item.id === event.card_id)?.name ?? event.card_id}
                    </p>
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      {t(`accountPool.supplier.${event.supplier}`)}
                    </p>
                  </td>
                  <td className="min-w-36 max-w-56 break-words px-3 py-3 font-mono">
                    {event.model ?? t("accountPool.dashboard.unknown")}
                  </td>
                  {(["input_tokens", "output_tokens", "cache_read_input_tokens"] as const).map((field) => (
                    <td key={field} className="whitespace-nowrap px-3 py-3 text-right font-mono tabular-nums">
                      {event[field] == null ? (
                        <span className="text-muted-foreground">{t("accountPool.dashboard.unknown")}</span>
                      ) : (
                        new Intl.NumberFormat(i18n.language).format(event[field])
                      )}
                    </td>
                  ))}
                  <td className="whitespace-nowrap px-3 py-3 text-right font-mono tabular-nums">
                    {formatCost(event.cost_usd) ?? "计价未知"}
                  </td>
                  <td className="whitespace-nowrap px-3 py-3 text-right font-mono tabular-nums">
                    {event.cache_rate == null ? (
                      <span className="text-muted-foreground">{t("accountPool.dashboard.unknown")}</span>
                    ) : (
                      `${(event.cache_rate * 100).toFixed(1)}%`
                    )}
                  </td>
                  <td className="whitespace-nowrap px-3 py-3 text-right tabular-nums">
                    {event.duration_ms == null ? t("accountPool.dashboard.unknown") : `${event.duration_ms} ms`}
                  </td>
                  <td className="whitespace-nowrap px-3 py-3">
                    <Badge variant={event.final_status === "failed" ? "destructive" : "secondary"}>
                      {t(`accountPool.logs.results.${event.final_status}`)}
                    </Badge>
                    <p className="mt-1 text-muted-foreground">{event.http_status}</p>
                  </td>
                  <td className="min-w-48 max-w-80 px-3 py-3">
                    <Button
                      variant="link"
                      className="h-auto max-w-full justify-start p-0 text-left text-xs"
                      title={event.message}
                      onClick={() => setEventId(String(event.event_id))}
                    >
                      <span className="line-clamp-2 whitespace-normal break-words">{event.message}</span>
                    </Button>
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      {t(`accountPool.logs.stages.${event.stage}`)}
                    </p>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {query.data.items.length === 0 && (
            <p className="p-6 text-center text-muted-foreground">{t("accountPool.logs.empty")}</p>
          )}
          <DataTablePagination
            page={Math.floor(offset / pageSize)}
            pageSize={pageSize}
            rowCount={query.data.total}
            showPageJump
            isLoading={query.isPending}
            onPageChange={(page) => setOffset(page * pageSize)}
            onPageSizeChange={(size) => {
              setPageSize(size);
              setOffset(0);
            }}
          />
        </div>
      )}
      <Dialog
        open={eventId !== null}
        onOpenChange={(open) => {
          if (!open) setEventId(null);
        }}
      >
        <DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>{t("accountPool.logs.detail")}</DialogTitle>
            <DialogDescription>{t("accountPool.logs.chain")}</DialogDescription>
          </DialogHeader>
          {detail.isPending && <p>{t("accountPool.management.loading")}</p>}
          {detail.isError && (
            <div role="alert">
              <p>{t("accountPool.logs.loadFailed")}</p>
              <Button onClick={() => void detail.refetch()}>{t("accountPool.retry")}</Button>
            </div>
          )}
          {detail.data && (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <p className="text-xs text-muted-foreground">
                  会话：{detail.data.event.session_id ?? "未关联"} · 代理：{detail.data.event.proxy_endpoint ?? "未知"}{" "}
                  · Usage：
                  {
                    (
                      {
                        synced: "已同步",
                        standard: "由 LiteLLM 统一记账",
                        failed: "同步失败",
                        pending: "等待同步",
                        unavailable: "数据库不可用",
                      } as Record<string, string>
                    )[detail.data.event.spend_sync_state ?? "pending"]
                  }
                </p>
                {["stored", "truncated"].includes(detail.data.event.full_log_state ?? "disabled") && (
                  <Button variant="outline" onClick={() => setFullEventId(detail.data.event.event_id ?? null)}>
                    查看完整日志
                  </Button>
                )}
                {detail.data.event.full_log_state === "failed" && <p role="alert">完整日志保存失败，日常日志已保留</p>}
              </div>
              <dl className="grid gap-1 text-sm">
                {(
                  [
                    "card_id",
                    "environment_id",
                    "account_id",
                    "card_key_id",
                    "request_id",
                    "upstream_request_id",
                    "trace_id",
                    "model",
                    "endpoint",
                    "method",
                    "http_status",
                    "upstream_code",
                    "duration_ms",
                    "input_tokens",
                    "output_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                    "routing_reason",
                    "cost_usd",
                  ] as const
                ).map((field) => (
                  <div className="grid grid-cols-[9rem_1fr] gap-2" key={field}>
                    <dt>{t(`accountPool.logs.${field}`)}</dt>
                    <dd className="break-all">
                      {detailFieldValue(detail.data.event, field, t("accountPool.management.unavailable"), (reason) =>
                        t(`accountPool.logs.routingReasons.${reason}`),
                      )}
                    </dd>
                  </div>
                ))}
              </dl>
              {detail.data.attempts.map((event) => (
                <article key={event.event_id} className="rounded-md border p-3 text-sm">
                  <p>
                    {t("accountPool.logs.attempt", { count: event.attempt })} ·{" "}
                    {formatDateTime(event.occurred_at, i18n.language)} · {t(`accountPool.logs.stages.${event.stage}`)} ·{" "}
                    {t(`accountPool.logs.results.${event.final_status}`)}
                  </p>
                  <p className="mt-2 break-words">{event.message}</p>
                  {event.detail && <p className="mt-2 break-words">{event.detail}</p>}
                  <p className="mt-2 text-muted-foreground">
                    {t("accountPool.logs.retryCount", { count: event.retry_count })} ·{" "}
                    {t("accountPool.logs.switched_account")}: {t(`accountPool.management.${event.switched_account}`)}
                  </p>
                  {event.routing_reason && (
                    <p className="mt-1 text-muted-foreground">
                      {t("accountPool.logs.routing_reason")}:{" "}
                      {t(`accountPool.logs.routingReasons.${event.routing_reason}`)}
                    </p>
                  )}
                </article>
              ))}
              {detail.data.has_more && <p>{t("accountPool.logs.moreAttempts")}</p>}
            </>
          )}
        </DialogContent>
      </Dialog>
      <FullLogDialog accessToken={accessToken} eventId={fullEventId} onClose={() => setFullEventId(null)} />
    </div>
  );
}
