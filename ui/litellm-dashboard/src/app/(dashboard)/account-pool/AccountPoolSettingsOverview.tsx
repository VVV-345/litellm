"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Check, ChevronDown, CircleHelp, Layers3, RefreshCw, Settings2 } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";
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
      "full_log_success_enabled",
      "full_log_sample_percent",
      "full_log_max_body_kb",
      "full_log_max_storage_mb",
      "daily_log_max_rows",
      "log_redact_fields",
      "runtime_log_level",
      "runtime_log_format",
      "runtime_log_console",
      "runtime_log_file",
      "runtime_log_max_mb",
      "runtime_log_backups",
      "runtime_log_stacktrace",
      "runtime_log_quiet_dependencies",
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
  full_log_success_enabled: "保存成功正文",
  full_log_sample_percent: "成功正文采样（%）",
  full_log_max_body_kb: "单方向正文上限（KB）",
  full_log_max_storage_mb: "正文与摘要容量上限（MB）",
  daily_log_max_rows: "日常日志最多记录数",
  log_redact_fields: "额外脱敏字段",
  runtime_log_level: "LiteLLM 日志级别",
  runtime_log_format: "LiteLLM 日志格式",
  runtime_log_console: "LiteLLM 控制台输出",
  runtime_log_file: "LiteLLM 文件输出",
  runtime_log_max_mb: "运行日志单文件上限（MB）",
  runtime_log_backups: "每个进程保留轮转文件数",
  runtime_log_stacktrace: "运行日志异常堆栈",
  runtime_log_quiet_dependencies: "第三方库仅警告和错误",
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

const nativeLabels: Record<string, string> = {
  account_pool_routing: "号池账号选择与会话亲和性",
  routing_strategy: "负载均衡策略",
  num_retries: "失败重试次数",
  retry_after: "重试等待（秒）",
  timeout: "请求超时（秒）",
  allowed_fails: "冷却前允许失败次数",
  cooldown_time: "冷却时间（秒）",
  fallbacks: "模型回退规则",
  context_window_fallbacks: "上下文超限回退",
  model_group_alias: "模型别名",
  model_group_retry_policy: "按模型重试策略",
  retry_policy: "按错误类型重试策略",
  routing_strategy_args: "负载均衡参数",
  max_parallel_requests: "最大并发请求",
  max_retries: "最大重试次数",
  model_group_affinity_config: "按模型会话亲和性",
  routing_groups: "路由分组",
  enable_tag_filtering: "标签过滤",
  tag_routing_prefix: "标签路由前缀",
  stream_timeout: "流式超时（秒）",
  max_fallbacks: "最大回退次数",
  content_policy_fallbacks: "内容策略回退",
  disable_cooldowns: "禁用冷却",
  enable_pre_call_checks: "调用前检查",
  enable_health_check_routing: "健康检查选路",
  health_check_staleness_threshold: "健康快照有效期（秒）",
  health_check_ignore_transient_errors: "忽略临时健康错误",
  enable_weighted_failover: "同模型部署故障切换",
  deployment_affinity_ttl_seconds: "会话亲和性有效期（秒）",
  optional_pre_call_checks: "可选调用前检查",
  set_verbose: "详细诊断日志",
  drop_params: "过滤不支持的参数",
  request_timeout: "默认请求超时",
  global_max_parallel_requests: "全局最大并发",
  master_key: "管理密钥",
  database_url: "数据库连接",
  alerting: "告警渠道",
  store_model_in_db: "数据库模型配置",
  background_health_checks: "后台健康检查",
  health_check_interval: "健康检查间隔",
  max_request_size_mb: "请求大小上限（MB）",
  max_batch_file_size_mb: "批处理文件上限（MB）",
  max_response_size_mb: "响应大小上限（MB）",
  proxy_config_reload_interval_seconds: "配置刷新间隔（秒）",
  allow_requests_on_db_unavailable: "数据库不可用时允许请求",
};

function SettingValue({ value }: { value: unknown }) {
  if (typeof value === "boolean")
    return (
      <span
        className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${value ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" : "bg-muted text-muted-foreground"}`}
      >
        {value && <Check className="size-3" aria-hidden="true" />}
        {value ? "开启" : "关闭"}
      </span>
    );
  const hasEntries = value !== null && typeof value === "object" && Object.keys(value).length > 0;
  if (hasEntries)
    return (
      <details className="group/value w-full rounded-lg bg-muted/40 text-left">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2 text-xs text-muted-foreground">
          {Array.isArray(value) ? `${value.length} 项配置` : `${Object.keys(value).length} 项规则`}
          <ChevronDown className="size-3.5 transition-transform group-open/value:rotate-180" aria-hidden="true" />
        </summary>
        <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-all border-t px-3 py-2 text-xs leading-relaxed">
          {valueText(value)}
        </pre>
      </details>
    );
  return (
    <span className={`text-sm ${value == null ? "text-muted-foreground" : "font-medium tabular-nums"}`}>
      {valueText(value)}
    </span>
  );
}

function OverviewCard({
  title,
  href,
  scope,
  children,
  legacy = false,
}: {
  title: string;
  href: string;
  scope: string;
  children: ReactNode;
  legacy?: boolean;
}) {
  return (
    <section
      className={`min-w-0 overflow-hidden rounded-xl border bg-card shadow-sm ${legacy ? "border-amber-500/30" : "border-border"}`}
    >
      <div className="border-b bg-muted/20 p-5">
        <Link
          href={settingHref(href)}
          className="group flex items-center justify-between gap-3 font-semibold hover:text-primary"
        >
          <span className="flex items-center gap-2.5">
            <span
              className={`rounded-lg p-2 ${legacy ? "bg-amber-500/10 text-amber-700" : "bg-primary/10 text-primary"}`}
            >
              <Settings2 className="size-4" aria-hidden="true" />
            </span>
            {title}
          </span>
          <ArrowUpRight className="size-4 shrink-0 text-muted-foreground group-hover:text-primary" aria-hidden="true" />
        </Link>
        <p className="mt-3 text-xs leading-5 text-muted-foreground">{scope}</p>
        {legacy && <p className="mt-2 text-xs font-medium text-amber-700 dark:text-amber-400">保留旧值 · 未完整接入</p>}
      </div>
      <div className="p-5">{children}</div>
    </section>
  );
}

type NativeField = { field_name: string; field_value: unknown };
const valueText = (value: unknown): string => {
  if (value === undefined) return "接口未返回";
  if (value === null) return "未设置 / 继承默认";
  if (typeof value === "boolean") return value ? "开启" : "关闭";
  if (typeof value === "object" && Object.keys(value).length === 0) return "无";
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
  const busy = runtime.isFetching || router.isFetching || general.isFetching;
  const failures = [runtime, router, general].filter((query) => query.isError).length;
  const runtimeFallback = runtime.isError ? "读取失败" : "正在读取…";
  const syncStatus = runtime.data?.requires_reload
    ? "需重新加载，请检查账号同步状态"
    : "已保存，执行状态以账号同步结果为准";
  const sources = busy ? "正在读取服务端" : "运行配置 · 路由 · 全局设置";
  return (
    <div className="space-y-6">
      <div className="rounded-2xl border bg-gradient-to-br from-primary/5 via-background to-background p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <span className="rounded-xl bg-primary/10 p-3 text-primary">
              <Layers3 className="size-5" aria-hidden="true" />
            </span>
            <div>
              <h2 className="text-xl font-semibold tracking-tight">全局设置总览</h2>
              <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
                一处查看当前设置，点击卡片标题前往修改。单卡、模型、团队和密钥的独立配置可能覆盖全局默认值。
              </p>
            </div>
          </div>
          <Button variant="outline" onClick={refresh} disabled={busy}>
            <RefreshCw className={`mr-2 size-4 ${busy ? "animate-spin" : ""}`} aria-hidden="true" />
            {busy ? "正在刷新" : "刷新总览"}
          </Button>
        </div>
        <div className="mt-5 grid gap-3 sm:grid-cols-3">
          <div className="rounded-xl border bg-background/80 p-4">
            <p className="text-xs text-muted-foreground">运行配置</p>
            <p className="mt-2 text-sm font-semibold">
              {runtime.data ? `配置版本 ${runtime.data.version}` : runtimeFallback}
            </p>
          </div>
          <div className="rounded-xl border bg-background/80 p-4">
            <p className="text-xs text-muted-foreground">保存与同步</p>
            <p className="mt-2 text-sm font-semibold">{runtime.data ? syncStatus : "等待配置数据"}</p>
          </div>
          <div className="rounded-xl border bg-background/80 p-4">
            <p className="text-xs text-muted-foreground">数据来源</p>
            <p className="mt-2 text-sm font-semibold">{failures ? `${failures} 个来源读取失败` : sources}</p>
          </div>
        </div>
        {runtime.data?.updated_at && (
          <p className="mt-3 text-xs text-muted-foreground">
            运行配置更新时间：{new Date(runtime.data.updated_at).toLocaleString("zh-CN")}
          </p>
        )}
      </div>
      {runtime.isPending && <p role="status">正在读取运行配置…</p>}
      {runtime.isError && (
        <p role="alert" className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm">
          运行配置读取失败，请刷新重试
        </p>
      )}
      <div className="grid items-start gap-4 md:grid-cols-2 2xl:grid-cols-3">
        <OverviewCard title="设置生效规则" href="router-settings?tab=loadbalancing" scope="当前请求执行关系">
          <dl className="space-y-3 text-sm">
            <div>
              <dt className="font-medium">选卡、顺序与会话保持</dt>
              <dd className="mt-1 text-muted-foreground">
                由 LiteLLM 原生路由控制，密钥或团队的独立规则可能覆盖全局默认。卡片上的旧路由值仅保留查看。
              </dd>
            </div>
            <div>
              <dt className="font-medium">单卡额度、并发、代理与模型</dt>
              <dd className="mt-1 text-muted-foreground">
                由卡片设置控制，转发前再次检查；供应商设置需同步成功才可视为已生效。
              </dd>
            </div>
            <div>
              <dt className="font-medium">虚拟密钥指定卡片</dt>
              <dd className="mt-1 text-muted-foreground">
                始终限制在所选卡片范围内。顺序偏好、重试和回退均不能扩大这个范围。
              </dd>
            </div>
          </dl>
        </OverviewCard>
        {[
          {
            title: "LiteLLM 路由、重试与回退",
            query: router,
            fields: router.data?.fields,
            href: "router-settings?tab=loadbalancing",
            scope: "原生路由当前值；团队或密钥可配置独立规则",
          },
          {
            title: "LiteLLM 全局设置",
            query: general,
            fields: general.data,
            href: "router-settings?tab=general",
            scope: "网关全局默认值，未设置的项目沿用系统默认",
          },
        ].map((section) => (
          <OverviewCard key={section.title} title={section.title} href={section.href} scope={section.scope}>
            {section.query.isPending && <p className="text-sm text-muted-foreground">正在读取…</p>}
            {section.query.isError && (
              <p role="alert" className="text-sm text-destructive">
                {section.title}读取失败，请刷新重试
              </p>
            )}
            <dl className="divide-y divide-border/60">
              {section.fields?.slice(0, 6).map((field) => (
                <div
                  key={field.field_name}
                  className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 py-3 first:pt-0 last:pb-0"
                >
                  <dt className="text-sm text-muted-foreground" title={field.field_name}>
                    {nativeLabels[field.field_name] ?? field.field_name}
                  </dt>
                  <dd className="min-w-0 max-w-full text-right">
                    <SettingValue value={field.field_value} />
                  </dd>
                </div>
              ))}
            </dl>
            {(section.fields?.length ?? 0) > 6 && (
              <details className="mt-4 border-t pt-3">
                <summary className="cursor-pointer text-xs font-medium text-primary">
                  查看其余 {(section.fields?.length ?? 0) - 6} 项设置
                </summary>
                <dl className="mt-3 space-y-3">
                  {section.fields?.slice(6).map((field) => (
                    <div key={field.field_name} className="space-y-1">
                      <dt className="break-words text-xs text-muted-foreground" title={field.field_name}>
                        {nativeLabels[field.field_name] ?? field.field_name}
                      </dt>
                      <dd>
                        <SettingValue value={field.field_value} />
                      </dd>
                    </div>
                  ))}
                </dl>
              </details>
            )}
          </OverviewCard>
        ))}
        {runtime.data &&
          groups.map((group) => (
            <OverviewCard
              key={group.title}
              title={group.title}
              href={group.href}
              scope={group.scope}
              legacy={group.title === "保留的旧路由值"}
            >
              <dl className="divide-y divide-border/60">
                {group.fields.map((field) => (
                  <div
                    key={field}
                    className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 py-3 first:pt-0 last:pb-0"
                  >
                    <dt className="text-sm text-muted-foreground">
                      {fieldLabels[field] ?? (field.endsWith("_profiles") ? "命名配置与卡片绑定" : field)}
                    </dt>
                    <dd className="min-w-0 max-w-full text-right">
                      <SettingValue value={runtime.data.values[field]} />
                    </dd>
                  </div>
                ))}
              </dl>
            </OverviewCard>
          ))}
      </div>
      <p className="flex items-start gap-2 rounded-xl bg-muted/40 p-4 text-xs leading-5 text-muted-foreground">
        <CircleHelp className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        总览展示服务端保存的当前值。修改后请同时检查卡片同步状态；标注为旧值的设置，不代表已经生效。
      </p>
    </div>
  );
}
