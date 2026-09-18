import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/components/networking";
import { Button } from "@/components/ui/button";
import { NumberSetting, ToggleSetting } from "@/components/Settings/RuntimeSettings/RuntimeSettingsFields";
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
      toast.success(
        saved.requires_reload ? "日志设置已保存，供应商运行日志选项需要重新加载账号环境后生效" : "日志设置已保存",
      );
    } catch (error) {
      toast.fromError(error);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-4 py-4">
      <h2 className="text-lg font-semibold">日志采集与保留</h2>
      <p className="text-sm text-muted-foreground">
        号池请求的完整正文、运行记录和清理策略在此管理。关闭正文采集不会删除历史记录；LiteLLM 调用费用继续统一记账。
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <ToggleSetting
          id="logging-full"
          label="记录完整日志（输入、提示词、回复和工具调用）"
          checked={values.full_logging_enabled}
          disabled={busy}
          onChange={(value) => update("full_logging_enabled", value)}
        />
        <ToggleSetting
          id="logging-skip-failed"
          label="失败请求不保存完整日志"
          checked={values.full_log_skip_failed}
          disabled={busy}
          onChange={(value) => update("full_log_skip_failed", value)}
        />
        <NumberSetting
          id="logging-daily-retention"
          label="运行日志保留天数"
          value={values.daily_log_retention_days}
          disabled={busy}
          onChange={(value) => update("daily_log_retention_days", value)}
        />
        <NumberSetting
          id="logging-full-retention"
          label="完整日志保留天数"
          value={values.full_log_retention_days}
          disabled={busy}
          onChange={(value) => update("full_log_retention_days", value)}
        />
      </div>
      <details className="rounded-md border p-4">
        <summary className="cursor-pointer font-medium">供应商运行日志</summary>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <ToggleSetting
            id="logging-file"
            label="写入运行日志文件"
            checked={values.file_logging_enabled}
            disabled={busy}
            onChange={(value) => update("file_logging_enabled", value)}
          />
          <ToggleSetting
            id="logging-debug"
            label="调试日志"
            checked={values.debug_logging_enabled}
            disabled={busy}
            onChange={(value) => update("debug_logging_enabled", value)}
          />
          <ToggleSetting
            id="logging-request"
            label="供应商请求日志"
            checked={values.request_log_enabled}
            disabled={busy}
            onChange={(value) => update("request_log_enabled", value)}
          />
          <ToggleSetting
            id="logging-usage"
            label="供应商用量统计"
            checked={values.usage_statistics_enabled}
            disabled={busy}
            onChange={(value) => update("usage_statistics_enabled", value)}
          />
          <NumberSetting
            id="logging-size"
            label="日志文件总大小上限（MB，0 为不限）"
            value={values.logs_max_total_size_mb}
            disabled={busy}
            onChange={(value) => update("logs_max_total_size_mb", value)}
          />
          <NumberSetting
            id="logging-files"
            label="错误日志文件上限"
            value={values.error_logs_max_files}
            disabled={busy}
            onChange={(value) => update("error_logs_max_files", value)}
          />
        </div>
      </details>
      <div>
        <Button disabled={busy || draft === null} onClick={() => void save()}>
          保存日志设置
        </Button>
      </div>
    </div>
  );
}
