/** 本文件渲染号池仪表盘的汇总指标和供应商分组，卡片操作由页面注入以保持权限边界。 */

import { useMemo, useState, type ReactNode } from "react";
import { ArrowLeftRight, CheckCircle2, CircleAlert, Coins, Gauge, Layers3, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Skeleton } from "@/components/ui/skeleton";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

import { formatCount, formatDateTime } from "../../utils/AccountPoolFormatters";
import type { AccountPoolQuotaRefreshStatus, ErrorStats } from "../../api/AccountPoolManagementApi";
import type { AccountPoolEnvironment, AccountPoolSupplier } from "../../utils/AccountPoolTypes";
import { AccountPoolSortableCards } from "../cards/AccountPoolSortableCards";
import dynamic from "next/dynamic";
import { loadAccountPoolQuotaPanel } from "../../preloadModules";
import { AccountPoolWorkspace } from "../shared/AccountPoolWorkspace";
import { AccountPoolRefreshIntervalSelect } from "../shared/AccountPoolRefreshIntervalSelect";
import { useAccountPoolQuotaInterval } from "../../hooks/useAccountPoolQuotaInterval";
import {
  filterAccountPoolDashboardEnvironments,
  groupAccountPoolEnvironments,
  summarizeAccountPoolDashboard,
  type AccountPoolDashboardFilters,
} from "../../utils/accountPoolDashboardSelectors";

const AccountPoolQuotaPanel = dynamic(loadAccountPoolQuotaPanel);

interface AccountPoolDashboardProps {
  accessToken?: string | null;
  statsDays?: number;
  onStatsDaysChange?: (days: number) => void;
  compactNumbers?: boolean;
  onCompactNumbersChange?: (compact: boolean) => void;
  environments: readonly AccountPoolEnvironment[];
  statsByCard: ReadonlyMap<string, ErrorStats>;
  statsLoading: boolean;
  standardSummary?: ErrorStats;
  gatewayLatency?: { p95_ms: number | null; p99_ms: number | null; sample_count: number };
  statsError?: boolean;
  renderCard: (environment: AccountPoolEnvironment, stats: ErrorStats | undefined) => ReactNode;
  quotaRefreshStatus: AccountPoolQuotaRefreshStatus | null;
  onRefreshQuotas: () => void;
  refreshingQuotas: boolean;
}

const formatInteger = (value: number): string => new Intl.NumberFormat("zh-CN").format(value);
const PAGE_SIZES = [4, 8, 12] as const;
const STATUS_FILTERS: readonly AccountPoolDashboardFilters["status"][] = [
  "all",
  "ready",
  "cooling_down",
  "awaiting_authorization",
  "error",
  "disabled",
  "provisioning",
  "validating",
  "deleting",
];
const ENABLED_FILTER_LABELS = {
  all: "accountPool.dashboard.allEnabledStates",
  enabled: "accountPool.dashboard.onlyEnabled",
  disabled: "accountPool.dashboard.onlyDisabled",
} as const;
const DEFAULT_FILTERS: AccountPoolDashboardFilters = { search: "", enabled: "all", status: "all" };

export const AccountPoolDashboard = ({
  accessToken,
  statsDays = 30,
  onStatsDaysChange,
  compactNumbers = true,
  onCompactNumbersChange,
  environments,
  statsByCard,
  statsLoading,
  standardSummary,
  gatewayLatency,
  statsError = false,
  renderCard,
  quotaRefreshStatus,
  onRefreshQuotas,
  refreshingQuotas,
}: AccountPoolDashboardProps) => {
  const { t, i18n } = useTranslation();
  const interval = useAccountPoolQuotaInterval(accessToken);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [pageSize, setPageSize] = useState<number>(4);
  const [pages, setPages] = useState<Partial<Record<AccountPoolSupplier, number>>>({});
  const filtered = useMemo(
    () => filterAccountPoolDashboardEnvironments(environments, filters),
    [environments, filters],
  );
  const visibleGroups = useMemo(() => groupAccountPoolEnvironments(filtered), [filtered]);
  const updateFilter = <K extends keyof AccountPoolDashboardFilters>(
    field: K,
    value: AccountPoolDashboardFilters[K],
  ) => {
    setFilters((current) => ({ ...current, [field]: value }));
    setPages({});
  };
  const clearFilters = () => {
    setFilters(DEFAULT_FILTERS);
    setPages({});
  };
  const summary = summarizeAccountPoolDashboard(environments, statsByCard, standardSummary);
  const successRate =
    statsError || summary.successRate === null
      ? t("accountPool.dashboard.unknown")
      : `${summary.successRate.toFixed(1)}%`;
  const metric = (value: number) =>
    statsError ? t("accountPool.dashboard.unknown") : formatCount(value, compactNumbers);
  const latencyMetric = (value: number | null | undefined) =>
    statsError || value == null ? t("accountPool.dashboard.unknown") : `${Math.round(value)} ms`;

  return (
    <div className="grid gap-5" data-testid="account-pool-dashboard">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-xs font-medium text-muted-foreground">调用统计</span>
        <Select
          value={String(statsDays)}
          onValueChange={(value) => {
            if (value) onStatsDaysChange?.(Number(value));
          }}
        >
          <SelectTrigger aria-label="统计天数" className="w-32">
            <SelectValue>近 {statsDays} 天</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {[1, 7, 14, 30, 90].map((days) => (
              <SelectItem key={days} value={String(days)}>
                近 {days} 天
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          size="sm"
          variant="outline"
          aria-pressed={compactNumbers}
          onClick={() => onCompactNumbersChange?.(!compactNumbers)}
        >
          {compactNumbers ? "数字：K/M/B" : "数字：完整"}
        </Button>
      </div>
      {statsError && (
        <p role="alert" className="text-sm text-destructive">
          调用统计读取失败，暂不可用；请刷新重试
        </p>
      )}
      <div className="grid grid-cols-2 divide-x divide-y overflow-hidden rounded-xl border bg-card sm:grid-cols-3 lg:grid-cols-6">
        {[
          { label: t("accountPool.dashboard.successRate"), value: successRate, icon: CheckCircle2 },
          { label: t("accountPool.dashboard.requests"), value: metric(summary.totalRequests), icon: ArrowLeftRight },
          { label: t("accountPool.dashboard.failures"), value: metric(summary.failedRequests), icon: CircleAlert },
          { label: t("accountPool.dashboard.traffic"), value: metric(summary.totalTokens), icon: Coins },
          { label: t("accountPool.dashboard.gatewayP95"), value: latencyMetric(gatewayLatency?.p95_ms), icon: Gauge },
          {
            label: t("accountPool.dashboard.cards"),
            value: `${formatInteger(summary.enabledCards)} / ${formatInteger(summary.totalCards)}`,
            icon: Layers3,
          },
        ].map(({ label, value, icon: Icon }) => (
          <div key={label} className="min-w-0 px-4 py-3">
            <div className="flex items-center justify-between gap-2 text-muted-foreground">
              <p className="text-xs">{label}</p>
              <Icon className="size-4 shrink-0 text-blue-600/80 dark:text-blue-400" aria-hidden="true" />
            </div>
            <div className="mt-1 text-xl font-semibold tracking-tight tabular-nums">
              {statsLoading ? <Skeleton className="h-7 w-16" /> : value}
            </div>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-muted/20 px-3 py-2.5">
        <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">{t("accountPool.dashboard.quotaRefresh")}</span>
          <span>
            {t("accountPool.dashboard.lastQuotaRefresh")}：
            {formatDateTime(quotaRefreshStatus?.last_completed_at, i18n.language)}
          </span>
          <span>
            {t("accountPool.dashboard.nextQuotaRefresh")}：
            {formatDateTime(quotaRefreshStatus?.next_refresh_at, i18n.language)}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <AccountPoolRefreshIntervalSelect
            value={quotaRefreshStatus?.interval_minutes ?? 5}
            onChange={(value) => interval.mutate(value)}
            disabled={interval.isPending || !accessToken || !quotaRefreshStatus}
            label={t("accountPool.quotas.refreshInterval")}
            formatOption={(minutes) => t("accountPool.quotas.everyMinutes", { minutes })}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onRefreshQuotas}
            disabled={refreshingQuotas || quotaRefreshStatus?.running}
          >
            <RefreshCw className={refreshingQuotas || quotaRefreshStatus?.running ? "animate-spin" : undefined} />
            {t("accountPool.dashboard.refreshQuotas")}
          </Button>
        </div>
        <details className="basis-full text-xs text-muted-foreground">
          <summary className="cursor-pointer">更多统计</summary>
          <p className="pt-2">LiteLLM 标准调用日志，近 {statsDays} 天；供应商额度独立刷新，网关延迟为近 7 天。</p>
          <p className="pt-2">
            {t("accountPool.dashboard.gatewayP99")}：{latencyMetric(gatewayLatency?.p99_ms)} ·{" "}
            {t("accountPool.dashboard.successes")}：{metric(summary.successfulRequests)} ·{" "}
            {t("accountPool.dashboard.gatewayLatencySamples", { count: gatewayLatency?.sample_count ?? 0 })}
          </p>
        </details>
      </div>

      {environments.length > 0 && (
        <details className="rounded-lg border px-3 py-2 text-sm">
          <summary className="cursor-pointer text-xs font-medium text-muted-foreground">供应商额度详情</summary>
          <div className="mt-3">
            <AccountPoolQuotaPanel environments={environments} />
          </div>
        </details>
      )}

      {environments.length > 0 && (
        <div className="grid gap-3">
          <div className="grid grid-cols-1 items-end gap-3 sm:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_10rem_11rem_8rem_auto]">
            <div className="grid gap-1.5">
              <Label htmlFor="dashboard-card-search">{t("accountPool.dashboard.cardSearch")}</Label>
              <Input
                id="dashboard-card-search"
                value={filters.search}
                placeholder={t("accountPool.dashboard.cardSearch")}
                onChange={(event) => updateFilter("search", event.target.value)}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="dashboard-enabled-filter">{t("accountPool.dashboard.enabledFilter")}</Label>
              <Select
                value={filters.enabled}
                onValueChange={(value) => {
                  if (value === "all" || value === "enabled" || value === "disabled") updateFilter("enabled", value);
                }}
              >
                <SelectTrigger
                  id="dashboard-enabled-filter"
                  aria-label={t("accountPool.dashboard.enabledFilter")}
                  className="w-full"
                >
                  <SelectValue>{t(ENABLED_FILTER_LABELS[filters.enabled])}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t("accountPool.dashboard.allEnabledStates")}</SelectItem>
                  <SelectItem value="enabled">{t("accountPool.dashboard.onlyEnabled")}</SelectItem>
                  <SelectItem value="disabled">{t("accountPool.dashboard.onlyDisabled")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="dashboard-status-filter">{t("accountPool.dashboard.runtimeStatusFilter")}</Label>
              <Select
                value={filters.status}
                onValueChange={(value) => {
                  const status = STATUS_FILTERS.find((item) => item === value);
                  if (status) updateFilter("status", status);
                }}
              >
                <SelectTrigger
                  id="dashboard-status-filter"
                  aria-label={t("accountPool.dashboard.runtimeStatusFilter")}
                  className="w-full"
                >
                  <SelectValue>{t(`accountPool.status.${filters.status}`)}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {STATUS_FILTERS.map((status) => (
                    <SelectItem key={status} value={status}>
                      {t(`accountPool.status.${status}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="dashboard-page-size">{t("accountPool.dashboard.cardsPerPage")}</Label>
              <Select
                value={String(pageSize)}
                onValueChange={(value) => {
                  const size = PAGE_SIZES.find((item) => String(item) === value);
                  if (size) {
                    setPageSize(size);
                    setPages({});
                  }
                }}
              >
                <SelectTrigger
                  id="dashboard-page-size"
                  aria-label={t("accountPool.dashboard.cardsPerPage")}
                  className="w-full"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PAGE_SIZES.map((size) => (
                    <SelectItem key={size} value={String(size)}>
                      {size}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button
              type="button"
              variant="outline"
              onClick={clearFilters}
              disabled={!filters.search && filters.enabled === "all" && filters.status === "all"}
            >
              {t("accountPool.dashboard.clearFilters")}
            </Button>
          </div>
          <p className="text-sm text-muted-foreground">
            {t("accountPool.dashboard.filteredCards", { matched: filtered.length, total: environments.length })}
          </p>
        </div>
      )}

      {visibleGroups.length === 0 ? (
        <div className="rounded-md border border-dashed p-10 text-center text-sm text-muted-foreground">
          {t(environments.length === 0 ? "accountPool.dashboard.empty" : "accountPool.noMatchingEnvironments")}
        </div>
      ) : (
        <AccountPoolWorkspace
          title="卡片"
          defaultValue={environments[0]?.supplier}
          defaultOpen
          items={visibleGroups.map((group) => ({
            value: group.supplier,
            label: t(`accountPool.supplier.${group.supplier}`),
            summary: `${group.environments.length}`,
            content: (
              <AccountPoolSortableCards
                supplier={group.supplier}
                cards={group.environments}
                pagination={{
                  page: pages[group.supplier] ?? 1,
                  pageSize,
                  onPageChange: (page) => setPages((current) => ({ ...current, [group.supplier]: page })),
                }}
              >
                {(environment) => renderCard(environment, statsByCard.get(environment.id))}
              </AccountPoolSortableCards>
            ),
          }))}
        />
      )}
    </div>
  );
};
