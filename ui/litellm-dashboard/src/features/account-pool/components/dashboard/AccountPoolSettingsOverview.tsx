/** 分类查看号池与 LiteLLM 全局设置，按需展开并跳转到唯一编辑入口。 */
"use client";

import { useQuery } from "@tanstack/react-query";
import {
  ArrowUpRight,
  Check,
  ChevronDown,
  CircleHelp,
  RefreshCw,
  Settings2,
  UserPlus,
  Boxes,
  Network,
  ScrollText,
  Gauge,
  KeyRound,
  Waves,
  SlidersHorizontal,
  ListFilter,
  LayoutGrid,
  Puzzle,
  Route,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { apiClient } from "@/components/networking";
import { Button } from "@/components/ui/button";
import { migratedHref } from "@/utils/migratedPages";
import { getAccountPoolSettings, type AccountPoolSettings } from "../../api/AccountPoolManagementApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import { AccountPoolAutoUpdatePanel } from "../releases/AccountPoolAutoUpdatePanel";
import { ModuleVisibilityPanel } from "./ModuleVisibilityPanel";
import { AccountPoolWorkspace } from "../shared/AccountPoolWorkspace";

type SettingsGroupKey =
  | "common"
  | "access"
  | "network"
  | "logs"
  | "quota"
  | "auth"
  | "streaming"
  | "advanced"
  | "payload"
  | "plugins";

type SettingsGroup = {
  key: SettingsGroupKey;
  icon: LucideIcon;
  href: string;
  fields: readonly (keyof AccountPoolSettings)[];
};
const groups: readonly SettingsGroup[] = [
  {
    key: "common",
    icon: UserPlus,
    href: "policy-center?section=common",
    fields: ["default_concurrency_limit", "default_model_discovery", "common_profiles"],
  },
  {
    key: "access",
    icon: Boxes,
    href: "policy-center?section=access",
    fields: ["oauth_excluded_models", "oauth_model_aliases", "oauth_request_scoped_errors", "access_profiles"],
  },
  {
    key: "network",
    icon: Network,
    href: "policy-center?section=network",
    fields: [
      "default_proxy_profile_id",
      "max_attempts",
      "request_timeout_seconds",
      "websocket_enabled",
      "network_profiles",
    ],
  },
  {
    key: "logs",
    icon: ScrollText,
    href: "logs?log_view=settings",
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
    key: "quota",
    icon: Gauge,
    href: "account-pool?tab=dashboard",
    fields: [
      "quota_switch_project",
      "quota_switch_preview_model",
      "quota_refresh_interval_minutes",
      "quota_reserve_percent",
      "quota_reserve_5h_percent",
      "quota_reserve_weekly_percent",
      "quota_snapshot_max_age",
      "quota_profiles",
    ],
  },
  {
    key: "auth",
    icon: KeyRound,
    href: "account-pool?tab=credentials",
    fields: ["auth_refresh_interval_minutes"],
  },
  {
    key: "streaming",
    icon: Waves,
    href: "policy-center?section=streaming",
    fields: ["streaming_enabled", "streaming_profiles", "streaming_rules", "sse_heartbeat_interval_seconds"],
  },
  {
    key: "advanced",
    icon: SlidersHorizontal,
    href: "policy-center?section=advanced",
    fields: ["websocket_auth_enabled", "force_model_prefix", "advanced_profiles"],
  },
  {
    key: "payload",
    icon: ListFilter,
    href: "policy-center?section=payload",
    fields: ["payload", "payload_profiles"],
  },
  {
    key: "plugins",
    icon: Puzzle,
    href: "account-pool?tab=releases&section=plugins",
    fields: ["plugins_enabled"],
  },
];

function SettingValue({ value, t }: { value: unknown; t: TFunction }) {
  if (typeof value === "boolean")
    return (
      <span
        className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${value ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" : "bg-muted text-muted-foreground"}`}
      >
        {value && <Check className="size-3" aria-hidden="true" />}
        {t(value ? "accountPool.settingsOverview.enabled" : "accountPool.settingsOverview.disabled")}
      </span>
    );
  const hasEntries = value !== null && typeof value === "object" && Object.keys(value).length > 0;
  if (hasEntries)
    return (
      <details className="group/value w-full rounded-lg bg-muted/40 text-left">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2 text-xs text-muted-foreground">
          {Array.isArray(value)
            ? t("accountPool.settingsOverview.entriesCount", { count: value.length })
            : t("accountPool.settingsOverview.rulesCount", { count: Object.keys(value).length })}
          <ChevronDown className="size-3.5 transition-transform group-open/value:rotate-180" aria-hidden="true" />
        </summary>
        <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-all border-t px-3 py-2 text-xs leading-relaxed">
          {valueText(value, t)}
        </pre>
      </details>
    );
  return (
    <span className={`text-sm ${value == null ? "text-muted-foreground" : "font-medium tabular-nums"}`}>
      {valueText(value, t)}
    </span>
  );
}

function OverviewCard({
  icon: Icon,
  title,
  href,
  scope,
  children,
  legacy = false,
  legacyNotice,
}: {
  title: string;
  href: string;
  scope: string;
  children: ReactNode;
  legacy?: boolean;
  legacyNotice?: string;
  icon: LucideIcon;
}) {
  return (
    <section
      className={`min-w-0 overflow-hidden rounded-xl border bg-card shadow-sm ${legacy ? "border-amber-500/30" : "border-border"}`}
    >
      <div className="border-b bg-muted/20 px-4 py-3">
        <Link
          href={settingHref(href)}
          className="group flex items-center justify-between gap-3 font-semibold hover:text-primary"
        >
          <span className="flex items-center gap-2.5">
            <span
              className={`rounded-lg p-2 ${legacy ? "bg-amber-500/10 text-amber-700" : "bg-primary/10 text-primary"}`}
            >
              <Icon className="size-4" aria-hidden="true" />
            </span>
            {title}
          </span>
          <ArrowUpRight className="size-4 shrink-0 text-muted-foreground group-hover:text-primary" aria-hidden="true" />
        </Link>
        <p className="mt-2 text-xs leading-5 text-muted-foreground">{scope}</p>
        {legacy && legacyNotice && (
          <p className="mt-2 text-xs font-medium text-amber-700 dark:text-amber-400">{legacyNotice}</p>
        )}
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

type NativeField = { field_name: string; field_value: unknown };
const valueText = (value: unknown, t: TFunction): string => {
  if (value === undefined) return t("accountPool.settingsOverview.notReturned");
  if (value === null) return t("accountPool.settingsOverview.unsetOrDefault");
  if (typeof value === "boolean") {
    return t(value ? "accountPool.settingsOverview.enabled" : "accountPool.settingsOverview.disabled");
  }
  if (typeof value === "object" && Object.keys(value).length === 0) return t("accountPool.settingsOverview.none");
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
};
const settingHref = (target: string) => {
  const [page, query] = target.split("?");
  return `${migratedHref(page)}${query ? `?${query}` : ""}`;
};

export function AccountPoolSettingsOverview({ accessToken }: { accessToken: string }) {
  const { t } = useTranslation();
  const runtime = useQuery({
    queryKey: accountPoolQueryKeys.settings(accessToken),
    queryFn: () => getAccountPoolSettings(accessToken),
    retry: false,
    staleTime: 0,
  });
  const router = useQuery({
    queryKey: accountPoolQueryKeys.nativeRouterSettings(accessToken),
    queryFn: () => apiClient.get<{ fields: NativeField[] }>("/router/settings", { accessToken }),
    retry: false,
  });
  const general = useQuery({
    queryKey: accountPoolQueryKeys.nativeGeneralSettings(accessToken),
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
  const runtimeFallback = t(
    runtime.isError ? "accountPool.settingsOverview.loadFailed" : "accountPool.settingsOverview.loading",
  );
  const syncStatus = t(
    runtime.data?.requires_reload
      ? "accountPool.settingsOverview.reloadRequired"
      : "accountPool.settingsOverview.savedCheckSync",
  );
  const sources = t(
    busy ? "accountPool.settingsOverview.readingServer" : "accountPool.settingsOverview.sourcesSummary",
  );
  const nativeSections = [
    {
      value: "router",
      title: t("accountPool.settingsOverview.nativeRouterTitle"),
      icon: Route,
      query: router,
      fields: router.data?.fields,
      href: "policy-center?section=router",
      scope: t("accountPool.settingsOverview.nativeRouterScope"),
    },
    {
      value: "general",
      title: t("accountPool.settingsOverview.nativeGeneralTitle"),
      icon: Settings2,
      query: general,
      fields: general.data,
      href: "policy-center",
      scope: t("accountPool.settingsOverview.nativeGeneralScope"),
    },
  ];
  return (
    <div className="grid min-w-0 gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{t("accountPool.settingsOverview.title")}</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            {runtime.data
              ? t("accountPool.settingsOverview.versionAndStatus", {
                  version: runtime.data.version,
                  status: syncStatus,
                })
              : runtimeFallback}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {failures ? t("accountPool.settingsOverview.sourcesFailed", { count: failures }) : sources}
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={refresh} disabled={busy}>
          <RefreshCw className={`size-4 ${busy ? "animate-spin" : ""}`} />
          {t(busy ? "accountPool.settingsOverview.refreshing" : "accountPool.settingsOverview.refresh")}
        </Button>
      </div>
      {runtime.isError && (
        <p role="alert" className="text-sm text-destructive">
          {t("accountPool.settingsOverview.runtimeLoadFailed")}
        </p>
      )}
      <AccountPoolWorkspace
        title={t("accountPool.settingsOverview.workspaceTitle")}
        items={[
          {
            value: "modules",
            icon: LayoutGrid,
            label: t("accountPool.settingsOverview.modulesTab"),
            summary: t("accountPool.settingsOverview.modulesSummary"),
            content: <ModuleVisibilityPanel accessToken={accessToken} />,
          },
          {
            value: "updates",
            icon: RefreshCw,
            label: t("accountPool.settingsOverview.updatesTab"),
            summary: t("accountPool.settingsOverview.updatesSummary"),
            content: <AccountPoolAutoUpdatePanel key={accessToken} accessToken={accessToken} />,
          },
          ...nativeSections.map((section) => ({
            value: section.value,
            label: section.title,
            icon: section.icon,
            summary: section.query.isError
              ? t("accountPool.settingsOverview.loadFailed")
              : t("accountPool.settingsOverview.itemsCount", { count: section.fields?.length ?? 0 }),
            content: (
              <OverviewCard
                icon={section.icon}
                title={section.title}
                href={section.href}
                scope={section.scope}
                legacyNotice={t("accountPool.settingsOverview.legacyNotice")}
              >
                {section.query.isPending && (
                  <p className="text-sm text-muted-foreground">{t("accountPool.settingsOverview.loading")}</p>
                )}
                {section.query.isError && (
                  <p role="alert" className="text-sm text-destructive">
                    {t("accountPool.settingsOverview.sectionLoadFailed", { title: section.title })}
                  </p>
                )}
                <dl className="divide-y divide-border/60">
                  {section.fields?.map((field) => (
                    <div key={field.field_name} className="flex flex-wrap items-start justify-between gap-3 py-2.5">
                      <dt className="text-sm text-muted-foreground" title={field.field_name}>
                        {t(`accountPool.settingsOverview.nativeFields.${field.field_name}`, {
                          defaultValue: field.field_name,
                        })}
                      </dt>
                      <dd className="min-w-0 max-w-full text-right">
                        <SettingValue value={field.field_value} t={t} />
                      </dd>
                    </div>
                  ))}
                </dl>
              </OverviewCard>
            ),
          })),
          ...groups.map((group) => {
            const title = t(`accountPool.settingsOverview.groups.${group.key}.title`);
            const scope = t(`accountPool.settingsOverview.groups.${group.key}.scope`);
            return {
              value: group.key,
              label: title,
              icon: group.icon,
              summary: t("accountPool.settingsOverview.itemsCount", { count: group.fields.length }),
              content: (
                <OverviewCard
                  icon={group.icon}
                  title={title}
                  href={group.href}
                  scope={scope}
                  legacyNotice={t("accountPool.settingsOverview.legacyNotice")}
                >
                  <dl className="divide-y divide-border/60">
                    {group.fields.map((field) => (
                      <div key={field} className="flex flex-wrap items-start justify-between gap-3 py-2.5">
                        <dt className="text-sm text-muted-foreground">
                          {t(`accountPool.settingsOverview.fields.${field}`, {
                            defaultValue: field.endsWith("_profiles")
                              ? t("accountPool.settingsOverview.namedProfiles")
                              : field,
                          })}
                        </dt>
                        <dd className="min-w-0 max-w-full text-right">
                          <SettingValue value={runtime.data ? runtime.data.values[field] : runtimeFallback} t={t} />
                        </dd>
                      </div>
                    ))}
                  </dl>
                </OverviewCard>
              ),
            };
          }),
        ]}
      />
      <p className="flex items-start gap-2 text-xs leading-5 text-muted-foreground">
        <CircleHelp className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
        {t("accountPool.settingsOverview.footerHint")}
      </p>
    </div>
  );
}
