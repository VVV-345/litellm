/** 展示脱敏浏览器事件，提供会话/卡片筛选及明确确认的按天清理。 */
"use client";

import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";
import { deleteManagerLogs, getManagerLogs } from "../../api/AccountPoolManagementApi";
import type { ManagerLogFilter, ManagerLogItem } from "../../api/AccountPoolManagementApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import { formatDateTime } from "../../utils/AccountPoolFormatters";

const PAGE_SIZE = 50;
const EMPTY_FILTER = { limit: PAGE_SIZE, offset: 0 };
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const steps: Readonly<Record<string, string>> = {
  compose: "启动隔离环境", login_script: "加载登录脚本", profile: "应用浏览器配置", browser_launch: "启动浏览器",
  page_navigation: "打开授权页", control_listener: "启动控制通道", driver: "浏览器驱动", authorize: "准备 OAuth",
  login: "识别登录页面", email: "输入邮箱", password: "输入密码", authenticator: "验证器 2FA",
  email_verification: "邮件验证", consent: "授权确认", callback: "OAuth 回调", credentials: "确认凭据",
  model_check: "同卡模型验收", cleanup: "清理授权会话",
};
const states: Readonly<Record<ManagerLogItem["state"], string>> = {
  started: "已开始", ready: "已就绪", running: "进行中", waiting: "等待人工", manual: "已接管",
  failed: "失败", expired: "超时", cancelled: "已取消", completed: "已完成",
};
const reasons: Readonly<Record<string, string>> = {
  browser_security_challenge: "检测到浏览器安全验证",
  email_verification: "需要邮件验证",
  navigation_http_error: "授权页返回 HTTP 错误",
  unexpected_origin: "跳转到未识别的站点",
  unrecognized_route: "未识别授权页面",
  form_or_transition_not_confirmed: "未确认表单或页面跳转",
  page_observation_failed: "无法读取页面状态",
  timeout: "此阶段等待超时",
  proxy_error: "代理连接失败",
  tls_error: "TLS 连接失败",
  process_error: "浏览器进程出错",
  docker_timeout: "等待容器就绪超时",
  compose_failed: "隔离容器启动失败",
  script_invalid: "已发布脚本包无法读取或校验",
  script_mismatch: "Worker 未确认发布的脚本版本",
  card_changed: "卡片配置或归属已变化",
  session_changed: "会话或出口绑定已变化",
  startup_interrupted: "启动被中断",
  worker_unreachable: "浏览器控制通道不可达",
  worker_not_running: "浏览器未运行或已结束",
  browser_driver_failed: "浏览器驱动运行失败",
  verification_required: "脚本无法继续，需要人工处理",
  account_verification_required: "无法确认当前账号或页面",
  manual_takeover: "操作员已接管",
  account_mismatch: "授权账号与任务不一致",
  confirmation_required: "需要人工确认授权",
  job_timeout: "自动化任务达到总时限",
  human_timeout: "人工处理时限已到",
  credentials_unavailable: "回调已消费，凭据未就绪",
  model_unavailable: "模型不可路由或仍在冷却",
  model_check_failed: "同卡模型验收未通过",
  model_check_interrupted: "模型验收中断或结果未知",
  browser_start_failed: "未能建立浏览器会话，请查看同会话的启动步骤",
  cleanup_pending: "会话清理未完成，Manager 会重试",
};
const severityBadges = { info: "outline", warning: "secondary", error: "destructive" } as const;
const severityLabels = { info: "信息", warning: "警告", error: "错误" };
const labelFor = (labels: Readonly<Record<string, string>>, key: string, otherwise: string) =>
  Object.hasOwn(labels, key) ? labels[key] : otherwise;

function LogRows({ items }: { items: ManagerLogItem[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>时间</TableHead><TableHead>级别</TableHead><TableHead>阶段 / 状态</TableHead>
          <TableHead>原因</TableHead><TableHead>会话 ID</TableHead><TableHead>卡片 ID</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((item) => (
          <TableRow key={item.event_id}>
            <TableCell className="whitespace-nowrap tabular-nums">{formatDateTime(item.occurred_at, "zh-CN")}</TableCell>
            <TableCell><Badge variant={severityBadges[item.severity]}>{severityLabels[item.severity]}</Badge></TableCell>
            <TableCell>{labelFor(steps, item.step, "未知阶段")} <span className="text-muted-foreground">· {states[item.state]}</span></TableCell>
            <TableCell className="min-w-40 whitespace-normal">
              {item.reason ? labelFor(reasons, item.reason, "原因未分类") : "未报告原因"}
              {item.http_status ? ` · HTTP ${item.http_status}` : ""}
              {item.duration_ms == null ? "" : ` · ${item.duration_ms} ms`}
            </TableCell>
            <TableCell className="font-mono text-xs">{item.session_id ?? "尚无会话"}</TableCell>
            <TableCell className="font-mono text-xs">{item.card_id}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export default function ManagerLogsPanel({ accessToken }: { accessToken: string }) {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<ManagerLogFilter>(EMPTY_FILTER);
  const [filterError, setFilterError] = useState(false);
  const [session, setSession] = useState("");
  const [card, setCard] = useState("");
  const [days, setDays] = useState("7");
  const daysToDelete = Number(days);
  const validDays = Number.isInteger(daysToDelete) && daysToDelete >= 1 && daysToDelete <= 365;
  const offset = filter.offset ?? 0;
  const logs = useQuery({
    queryKey: accountPoolQueryKeys.managerLogs(accessToken, filter),
    queryFn: ({ signal }) => getManagerLogs(accessToken, filter, signal),
    retry: false,
  });
  const purge = useMutation({
    mutationFn: (period: number) => deleteManagerLogs(accessToken, period),
    onSuccess: async () => {
      setFilter((current) => ({ ...current, offset: 0 }));
      await queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.managerLogsRoot(accessToken) });
    },
  });

  const applyFilters = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const sessionId = session.trim();
    const cardId = card.trim();
    const invalid = [sessionId, cardId].some((id) => id !== "" && !UUID.test(id));
    setFilterError(invalid);
    if (!invalid) setFilter({ ...EMPTY_FILTER, session_id: sessionId || undefined, card_id: cardId || undefined });
  };

  return (
    <section className="mx-auto max-w-7xl space-y-5 p-4 sm:p-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Manager 日志</h1>
        <p className="mt-1 text-sm text-muted-foreground">查看浏览器自动化的启动、登录、验证和回调进度。这里只显示脱敏事件。</p>
      </header>
      <form
        className="flex flex-wrap items-end gap-3 rounded-lg border bg-card p-4"
        onSubmit={applyFilters}
      >
        <div className="min-w-48 flex-1 space-y-1.5">
          <Label htmlFor="manager-log-session">会话 ID</Label>
          <Input id="manager-log-session" value={session} onChange={(event) => setSession(event.target.value)} placeholder="完整会话 UUID" />
        </div>
        <div className="min-w-48 flex-1 space-y-1.5">
          <Label htmlFor="manager-log-card">卡片 ID</Label>
          <Input id="manager-log-card" value={card} onChange={(event) => setCard(event.target.value)} placeholder="完整卡片 UUID" />
        </div>
        <Button type="submit">筛选</Button>
        <Button type="button" variant="outline" disabled={logs.isFetching} onClick={() => void logs.refetch()}>刷新</Button>
        {filterError ? <p role="alert" className="w-full text-sm text-destructive">请输入完整的会话或卡片 UUID。</p> : null}
      </form>
      <div className="overflow-hidden rounded-lg border bg-card">
        {logs.isError ? <p role="alert" className="p-4 text-sm text-destructive">无法加载 Manager 日志，请刷新后重试。</p> : null}
        {logs.isPending ? <p role="status" className="p-4 text-sm text-muted-foreground">正在加载日志…</p> : null}
        {!logs.isError && logs.data?.items.length === 0 ? (
          <p className="p-4 text-sm text-muted-foreground">没有符合条件的日志。此处不导入历史容器日志。</p>
        ) : null}
        {!logs.isError && logs.data?.items.length ? <LogRows items={logs.data.items} /> : null}
        <footer className="flex items-center justify-between gap-2 border-t px-4 py-3">
          <span className="text-sm text-muted-foreground">按时间倒序 · 第 {offset / PAGE_SIZE + 1} 页</span>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" disabled={offset === 0 || logs.isFetching}
              onClick={() => setFilter({ ...filter, offset: offset - PAGE_SIZE })}>上一页</Button>
            <Button size="sm" variant="outline" disabled={!logs.data?.has_more || logs.isFetching || offset >= 10_000}
              onClick={() => setFilter({ ...filter, offset: offset + PAGE_SIZE })}>下一页</Button>
          </div>
        </footer>
      </div>
      <div className="space-y-3 rounded-lg border bg-card p-4">
        <h2 className="font-semibold">清理 Manager 日志</h2>
        <p className="text-sm text-muted-foreground">按日志年龄清理所有卡片，不受上方筛选影响，也不会删除模型请求日志。</p>
        <div className="flex flex-wrap items-center gap-2">
          <Label htmlFor="manager-log-days">删除多少天前</Label>
          <Input id="manager-log-days" type="number" min={1} max={365} step={1}
            className="w-24" value={days} onChange={(event) => setDays(event.target.value)} />
          <AlertDialog>
            <AlertDialogTrigger render={<Button variant="outline" />} disabled={!validDays || purge.isPending}>
              清理旧日志
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>删除 {daysToDelete} 天前的 Manager 日志？</AlertDialogTitle>
                <AlertDialogDescription>会从所有卡片删除早于该时间的浏览器事件，保留更新的日志。删除后不能恢复。</AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>取消</AlertDialogCancel>
                <AlertDialogAction variant="destructive" onClick={() => purge.mutate(daysToDelete)}>确认删除</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
        {purge.isSuccess ? <p role="status" className="text-sm">已删除 {purge.data.deleted} 条 Manager 日志。</p> : null}
        {purge.isError ? <p role="alert" className="text-sm text-destructive">清理失败，请重试。</p> : null}
      </div>
    </section>
  );
}
