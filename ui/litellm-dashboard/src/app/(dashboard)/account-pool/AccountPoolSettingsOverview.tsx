"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { apiClient } from "@/components/networking";
import { Button } from "@/components/ui/button";
import { migratedHref } from "@/utils/migratedPages";
import { getAccountPoolSettings, type AccountPoolSettings } from "./AccountPoolManagementApi";

type SettingsGroup = {
  title: string;
  href: string;
  scope: string;
  fields: readonly (keyof AccountPoolSettings)[];
};
const groups: readonly SettingsGroup[] = [
  {
    title: "新账号默认值",
    href: "account-pool?tab=providers&section=common",
    scope: "创建账号时采用；已有账号以卡片配置为准",
    fields: ["default_concurrency_limit", "default_model_discovery", "common_profiles"],
  },
  {
    title: "供应商模型与访问",
    href: "models-and-endpoints?tab=model-group-alias",
    scope: "供应商模型别名、排除规则及按卡片绑定的命名配置",
    fields: ["oauth_excluded_models", "oauth_model_aliases", "oauth_request_scoped_errors", "access_profiles"],
  },
  {
    title: "出站网络",
    href: "models-and-endpoints?tab=retry-settings",
    scope: "全局默认值可被卡片及命名配置覆盖；调用重试使用 LiteLLM 策略",
    fields: ["default_proxy_profile_id", "request_timeout_seconds", "websocket_enabled", "network_profiles"],
  },
  {
    title: "日常日志与完整日志",
    href: "logs?log_view=settings",
    scope: "完整日志与供应商运行日志配置；费用统计取自 LiteLLM 调用记录",
    fields: [
      "full_logging_enabled",
      "full_log_skip_failed",
      "daily_log_retention_days",
      "full_log_retention_days",
      "file_logging_enabled",
      "debug_logging_enabled",
      "request_log_enabled",
      "usage_statistics_enabled",
      "logs_max_total_size_mb",
      "error_logs_max_files",
    ],
  },
  {
    title: "额度与刷新",
    href: "account-pool?tab=quotas&section=quota",
    scope: "定时刷新正常执行；切换项目及预览模型的旧值不生效，执行时固定关闭",
    fields: ["quota_switch_project", "quota_switch_preview_model", "quota_refresh_interval_minutes", "quota_profiles"],
  },
  {
    title: "认证刷新",
    href: "account-pool?tab=credentials",
    scope: "认证文件定时刷新",
    fields: ["auth_refresh_interval_minutes"],
  },
  {
    title: "流式传输",
    href: "router-settings?tab=streaming",
    scope: "全局流式开关，可由卡片绑定的命名配置覆盖",
    fields: ["streaming_enabled", "streaming_profiles", "streaming_rules"],
  },
  {
    title: "高级运行设置",
    href: "router-settings?tab=general",
    scope: "供应商运行参数；旧重试字段不再决定网关重试",
    fields: ["websocket_auth_enabled", "force_model_prefix", "advanced_profiles"],
  },
  {
    title: "请求参数",
    href: "router-settings?tab=payload",
    scope: "默认参数、强制覆盖、过滤规则及命名配置",
    fields: ["payload", "payload_profiles"],
  },
  {
    title: "运行插件",
    href: "router-settings?tab=general",
    scope: "号池运行插件列表；插件启用开关在 Router Settings 的通用设置中",
    fields: ["plugins_enabled"],
  },
  {
    title: "保留的旧路由值",
    href: "router-settings?tab=loadbalancing",
    scope: "以下旧值仍保留，但不参与当前跨卡选卡与重试；default_route 仍可能影响卡片内部凭证顺序",
    fields: ["default_route", "max_attempts", "request_retry", "max_retry_credentials", "max_retry_interval"],
  },
];

const fieldLabels: Partial<Record<keyof AccountPoolSettings, string>> = {
  default_concurrency_limit: "默认并发上限",
  default_model_discovery: "默认自动发现模型",
  default_proxy_profile_id: "默认出站代理",
  request_timeout_seconds: "请求超时（秒）",
  websocket_enabled: "默认允许 WebSocket",
  oauth_excluded_models: "供应商排除模型",
  oauth_model_aliases: "供应商模型别名",
  oauth_request_scoped_errors: "请求范围错误规则",
  full_logging_enabled: "完整日志",
  full_log_skip_failed: "完整日志跳过失败请求",
  daily_log_retention_days: "日常日志保留天数",
  full_log_retention_days: "完整日志保留天数",
  file_logging_enabled: "写入日志文件",
  debug_logging_enabled: "供应商调试日志",
  request_log_enabled: "供应商请求日志",
  usage_statistics_enabled: "供应商用量统计",
  logs_max_total_size_mb: "日志容量上限（MB）",
  error_logs_max_files: "错误日志文件上限",
  quota_switch_project: "额度不足切换项目（旧值）",
  quota_switch_preview_model: "额度不足切换预览模型（旧值）",
  quota_refresh_interval_minutes: "额度刷新间隔（分钟）",
  auth_refresh_interval_minutes: "认证刷新间隔（分钟）",
  streaming_enabled: "允许流式传输",
  streaming_rules: "历史流式规则",
  websocket_auth_enabled: "WebSocket 鉴权",
  force_model_prefix: "强制模型前缀",
  payload: "请求参数规则",
  plugins_enabled: "运行插件启用",
  default_route: "旧默认凭证路由",
  max_attempts: "旧请求尝试上限",
  request_retry: "旧上游重试次数",
  max_retry_credentials: "旧重试凭证数",
  max_retry_interval: "旧重试最大间隔",
};

type NativeField = { field_name: string; field_value: unknown };
const valueText = (value: unknown): string => {
  if (value === undefined) return "接口未返回";
  if (value === null) return "未设置 / 继承默认";
  if (typeof value === "boolean") return value ? "开启" : "关闭";
  if (Array.isArray(value) && value.length === 0) return "无";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
};
const settingHref = (target: string) => {
  const [page, query] = target.split("?");
  return `${migratedHref(page)}${query ? `?${query}` : ""}`;
};

export function AccountPoolSettingsOverview({ accessToken }: { accessToken: string }) {
  const runtime = useQuery({
    queryKey: ["account-pool", "settings", accessToken],
    queryFn: () => getAccountPoolSettings(accessToken),
    retry: false,
    staleTime: 0,
  });
  const router = useQuery({
    queryKey: ["account-pool", "native-router-settings", accessToken],
    queryFn: () => apiClient.get<{ fields: NativeField[] }>("/router/settings", { accessToken }),
    retry: false,
  });
  const general = useQuery({
    queryKey: ["account-pool", "native-general-settings", accessToken],
    queryFn: () => apiClient.get<NativeField[]>("/config/list?config_type=general_settings", { accessToken }),
    retry: false,
  });
  const refresh = () => {
    void runtime.refetch();
    void router.refetch();
    void general.refetch();
  };
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">全局设置总览</h2>
          <p className="text-sm text-muted-foreground">
            显示服务端当前返回的全局配置。卡片、模型、团队或密钥可有独立覆盖；保存状态不等于所有账号已同步。
          </p>
        </div>
        <Button variant="outline" onClick={refresh}>
          刷新总览
        </Button>
      </div>
      {runtime.isPending && <p>正在读取运行配置…</p>}
      {runtime.isError && <p role="alert">运行配置读取失败，请刷新重试</p>}
      {runtime.data && (
        <>
          <p className="text-sm">
            配置版本 {runtime.data.version} · 更新时间 {runtime.data.updated_at ?? "未记录"} ·{" "}
            {runtime.data.requires_reload ? "需重新加载，请检查账号同步状态" : "已保存，请以各账号同步状态核对执行情况"}
          </p>
          <div className="grid gap-4 lg:grid-cols-2">
            {groups.map((group) => (
              <section key={group.title} className="space-y-3 rounded-lg border p-4">
                <Link className="font-medium text-primary underline" href={settingHref(group.href)}>
                  {group.title}
                </Link>
                <p className="text-sm text-muted-foreground">{group.scope}</p>
                <dl className="space-y-2">
                  {group.fields.map((field) => (
                    <div key={field}>
                      <dt className="text-sm">
                        {fieldLabels[field] ?? (field.endsWith("_profiles") ? "命名配置与卡片绑定" : field)}
                      </dt>
                      <dd className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-muted/30 p-2 text-xs">
                        {valueText(runtime.data.values[field])}
                      </dd>
                    </div>
                  ))}
                </dl>
              </section>
            ))}
          </div>
        </>
      )}
      {[
        {
          title: "LiteLLM 路由、重试与回退",
          query: router,
          fields: router.data?.fields,
          href: "router-settings?tab=loadbalancing",
        },
        { title: "LiteLLM 全局设置", query: general, fields: general.data, href: "router-settings?tab=general" },
      ].map((section) => (
        <section key={section.title} className="space-y-3 rounded-lg border p-4">
          <Link className="font-medium text-primary underline" href={settingHref(section.href)}>
            {section.title}
          </Link>
          {section.query.isPending && <p>正在读取…</p>}
          {section.query.isError && <p role="alert">{section.title}读取失败，请刷新重试</p>}
          <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {section.fields?.map((field) => (
              <div key={field.field_name}>
                <dt className="text-sm">{field.field_name}</dt>
                <dd className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-muted/30 p-2 text-xs">
                  {valueText(field.field_value)}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </div>
  );
}
