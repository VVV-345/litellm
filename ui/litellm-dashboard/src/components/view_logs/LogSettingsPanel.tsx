import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/components/networking";
import { Button } from "@/components/ui/button";
import { ToggleSetting } from "@/components/Settings/RuntimeSettings/RuntimeSettingsFields";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/lib/toast";
import type { components } from "@/lib/http/schema";

type Settings = components["schemas"]["RequestLogSettings"];
type SettingsView = components["schemas"]["RequestLogSettingsView"];

export function LogSettingsPanel({ accessToken }: { accessToken: string }) {
  const query = useQuery({
    queryKey: ["logs", "settings", accessToken],
    queryFn: () => apiClient.get<SettingsView>("/logs/settings", { accessToken }),
    retry: false,
  });
  const [draft, setDraft] = useState<Settings | null>(null);
  const [redactDraft, setRedactDraft] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const values = draft ?? query.data?.values;
  if (query.isPending) return <p role="status">正在读取日志设置…</p>;
  if (!values || query.isError) return <p role="alert">日志设置读取失败，请刷新重试</p>;

  const update = <K extends keyof Settings>(key: K, value: Settings[K]) => setDraft({ ...values, [key]: value });
  const save = async () => {
    if (!query.data) return;
    setBusy(true);
    try {
      const saved = await apiClient.put<SettingsView>("/logs/settings", {
        accessToken,
        body: { version: query.data.version, values },
      });
      await query.refetch();
      setDraft(null);
      setRedactDraft(null);
      toast.success(
        saved.requires_reload ? "日志设置已保存，供应商运行日志选项需要重新加载账号环境后生效" : "日志设置已保存",
      );
    } catch (error) {
      toast.fromError(error);
    } finally {
      setBusy(false);
    }
  };

  const number = (key: keyof Settings, label: string, min: number, max: number) => (
    <div className="space-y-2" key={key}>
      <Label htmlFor={`log-${key}`}>{label}</Label>
      <Input
        id={`log-${key}`}
        type="number"
        min={min}
        max={max}
        value={Number(values[key])}
        disabled={busy}
        onChange={(event) => update(key, Number(event.target.value))}
      />
    </div>
  );
  const toggle = (key: keyof Settings, label: string) => (
    <ToggleSetting
      key={key}
      id={`log-${key}`}
      label={label}
      checked={Boolean(values[key])}
      disabled={busy}
      onChange={(value) => update(key, value)}
    />
  );
  const select = (key: "runtime_log_level" | "runtime_log_format", label: string, options: readonly string[]) => (
    <div className="space-y-2">
      <Label htmlFor={`log-${key}`}>{label}</Label>
      <select
        id={`log-${key}`}
        className="h-9 w-full rounded-md border bg-background px-3"
        value={values[key]}
        disabled={busy}
        onChange={(event) => update(key, event.target.value as Settings[typeof key])}
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {option === "inherit" ? "沿用部署配置" : option}
          </option>
        ))}
      </select>
    </div>
  );
  return (
    <div className="space-y-5 py-4">
      <p className="text-sm text-muted-foreground">
        两类日志分别设置。费用继续由 LiteLLM 完整记账，关闭正文采集不会删除历史记录。
      </p>
      <Card>
        <CardHeader>
          <CardTitle>日常日志设置</CardTitle>
          <CardDescription>
            保留每次请求的模型、卡片、耗时、费用、请求 / 会话 ID、状态和重试原因，不采样计费记录。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            {number("daily_log_retention_days", "日常日志保留天数", 1, 3650)}
            {number("daily_log_max_rows", "日常日志最多记录数（0 为不限）", 0, 10000000)}
          </div>
          <p className="text-xs text-muted-foreground">
            清理按时间从旧到新执行，每分钟分批检查；不删除 LiteLLM 费用表。
          </p>
          <details className="rounded-lg border p-4">
            <summary className="cursor-pointer font-medium">服务运行日志（LiteLLM）</summary>
            <p className="my-3 text-xs text-muted-foreground">
              作用于 LiteLLM 进程输出，不过滤上方的请求记录。保存后约一分钟同步到各工作进程。
            </p>
            <div className="grid gap-4 sm:grid-cols-2">
              {select("runtime_log_level", "日志级别", ["inherit", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])}
              {select("runtime_log_format", "日志格式", ["inherit", "text", "json"])}
              {toggle("runtime_log_console", "输出到控制台（容器日志）")}
              {toggle("runtime_log_file", "写入 LiteLLM 运行日志文件")}
              {number("runtime_log_max_mb", "单个运行日志文件上限（MB）", 1, 1000)}
              {number("runtime_log_backups", "每个进程保留的轮转文件数", 1, 100)}
              {toggle("runtime_log_stacktrace", "输出异常堆栈（仍进行凭证脱敏）")}
              {toggle("runtime_log_quiet_dependencies", "第三方网络库仅记录 WARNING 及以上")}
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              文件异步写入并按大小轮转，队列满时丢弃运行输出以保护请求；JSON 包含时间、模块和关联 ID。远程日志使用
              LiteLLM 已有 Logging 集成配置。
            </p>
          </details>
          <details className="rounded-lg border p-4">
            <summary className="cursor-pointer font-medium">供应商运行日志（CLIProxyAPI）</summary>
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              {toggle("file_logging_enabled", "写入供应商运行日志文件")}
              {toggle("debug_logging_enabled", "供应商调试日志")}
              {toggle("request_log_enabled", "供应商请求日志")}
              {toggle("usage_statistics_enabled", "供应商用量统计")}
              {number("logs_max_total_size_mb", "供应商日志文件总上限（MB，0 为不限）", 0, 100000)}
              {number("error_logs_max_files", "供应商错误日志文件上限", 0, 10000)}
            </div>
            <p className="mt-3 text-xs text-muted-foreground">沿用卡片配置同步；保存结果会提示是否需要重新加载。</p>
          </details>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>完整日志设置</CardTitle>
          <CardDescription>控制输入、回复和工具历史的正文保存，用于会话阅读和导出。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            {toggle("full_logging_enabled", "记录完整日志（输入、提示词、回复和工具调用）")}
            {toggle("full_log_success_enabled", "保存成功请求正文")}
            {toggle("full_log_skip_failed", "失败请求不保存完整日志")}
            {number("full_log_sample_percent", "成功请求正文采样比例（%，同会话一致）", 0, 100)}
            {number("full_log_retention_days", "完整日志保留天数", 1, 3650)}
            {number("full_log_max_body_kb", "单方向正文采集上限（KB）", 1, 32768)}
            {number("full_log_max_storage_mb", "正文与摘要容量上限（MB，0 为不限）", 0, 100000)}
          </div>
          <div className="space-y-2">
            <Label htmlFor="log-redact-fields">额外脱敏字段（逗号分隔，如 email, phone）</Label>
            <Input
              id="log-redact-fields"
              disabled={busy}
              value={redactDraft ?? values.log_redact_fields.join(", ")}
              onChange={(event) => {
                setRedactDraft(event.target.value);
                update(
                  "log_redact_fields",
                  event.target.value
                    .split(",")
                    .map((field) => field.trim())
                    .filter(Boolean),
                );
              }}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            密钥、密码和加密思考签名始终脱敏。正文已有 gzip
            压缩，后台线程写入；超限会标记截断。容量限制按存储内容计算，每分钟分批清理，数据库释放的页供后续复用。
          </p>
          <p className="text-xs text-muted-foreground">
            失败正文不参与成功采样。关闭失败正文后，日常日志仍保存错误码、来源绑定和恢复失败原因。采集设置对后续请求生效，不补录过去正文。
          </p>
        </CardContent>
      </Card>
      <Button disabled={busy || draft === null} onClick={() => void save()}>
        保存日志设置
      </Button>
    </div>
  );
}
