/** 本文件渲染号池仪表盘的汇总指标和供应商分组，卡片操作由页面注入以保持权限边界。 */

import { useMemo, useState, type ReactNode } from "react";
import { Activity, AlertTriangle, Gauge, Layers3, Radio, RefreshCw, TrafficCone } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

import { formatDateTime } from "../../utils/AccountPoolFormatters";
import type { AccountPoolQuotaRefreshStatus, ErrorStats } from "../../api/AccountPoolManagementApi";
import type { AccountPoolEnvironment, AccountPoolSupplier } from "../../utils/AccountPoolTypes";
import { AccountPoolSortableCards } from "../cards/AccountPoolSortableCards";
import { AccountPoolSupplierLogo } from "../providers/AccountPoolSupplierLogo";
import { filterAccountPoolDashboardEnvironments, groupAccountPoolEnvironments, summarizeAccountPoolDashboard, type AccountPoolDashboardFilters } from "../../utils/accountPoolDashboardSelectors";

interface AccountPoolDashboardProps {
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
  "all", "ready", "cooling_down", "awaiting_authorization", "error", "disabled", "provisioning", "validating", "deleting",
];
const DEFAULT_FILTERS: AccountPoolDashboardFilters = { search: "", enabled: "all", status: "all" };

export const AccountPoolDashboard = ({
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
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [pageSize, setPageSize] = useState<number>(4);
  const [pages, setPages] = useState<Partial<Record<AccountPoolSupplier, number>>>({});
  const filtered = useMemo(() => filterAccountPoolDashboardEnvironments(environments, filters), [environments, filters]);
  const visibleGroups = useMemo(() => groupAccountPoolEnvironments(filtered), [filtered]);
  const updateFilter = <K extends keyof AccountPoolDashboardFilters>(field: K, value: AccountPoolDashboardFilters[K]) => {
    setFilters((current) => ({ ...current, [field]: value }));
    setPages({});
  };
  const clearFilters = () => {
    setFilters(DEFAULT_FILTERS);
    setPages({});
  };
  const summary = summarizeAccountPoolDashboard(environments, statsByCard, standardSummary);
  const groups = groupAccountPoolEnvironments(environments);
  const successRate =
    statsError || summary.successRate === null
      ? t("accountPool.dashboard.unknown")
      : `${summary.successRate.toFixed(1)}%`;
  const metric = (value: number) => (statsError ? t("accountPool.dashboard.unknown") : formatInteger(value));
  const latencyMetric = (value: number | null | undefined) =>
    statsError || value == null ? t("accountPool.dashboard.unknown") : `${Math.round(value)} ms`;

  return (
    <div className="grid gap-5" data-testid="account-pool-dashboard">
      <p className="text-sm text-muted-foreground">调用统计：LiteLLM 标准日志，近 30 天；供应商额度按卡片独立刷新</p>
      {statsError && (
        <p role="alert" className="text-sm text-destructive">
          调用统计读取失败，暂不可用；请刷新重试
        </p>
      )}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <Gauge className="size-5 text-emerald-600" />
            <div>
              <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.successRate")}</p>
              <p className="text-2xl font-semibold">{statsLoading ? <Skeleton className="h-7 w-16" /> : successRate}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <AlertTriangle className="size-5 text-destructive" />
            <div>
              <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.failures")}</p>
              <p className="text-2xl font-semibold">
                {statsLoading ? <Skeleton className="h-7 w-16" /> : metric(summary.failedRequests)}
              </p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <TrafficCone className="size-5 text-blue-600" />
            <div>
              <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.traffic")}</p>
              <p className="text-2xl font-semibold">
                {statsLoading ? <Skeleton className="h-7 w-16" /> : metric(summary.totalTokens)}
              </p>
              <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.tokens")}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <Layers3 className="size-5 text-violet-600" />
            <div>
              <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.cards")}</p>
              <p className="text-2xl font-semibold">
                {formatInteger(summary.enabledCards)} / {formatInteger(summary.totalCards)}
              </p>
              <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.enabled")}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <Gauge className="size-5 text-amber-600" />
            <div>
              <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.gatewayP95")}</p>
              <p className="text-2xl font-semibold">
                {statsLoading ? <Skeleton className="h-7 w-16" /> : latencyMetric(gatewayLatency?.p95_ms)}
              </p>
              <p className="text-xs text-muted-foreground">
                {t("accountPool.dashboard.gatewayLatencySamples", {
                  count: statsError ? t("accountPool.dashboard.unknown") : (gatewayLatency?.sample_count ?? 0),
                })}
              </p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <Gauge className="size-5 text-orange-600" />
            <div>
              <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.gatewayP99")}</p>
              <p className="text-2xl font-semibold">
                {statsLoading ? <Skeleton className="h-7 w-16" /> : latencyMetric(gatewayLatency?.p99_ms)}
              </p>
              <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.gatewayLatencyWindow")}</p>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <CardTitle className="text-base">{t("accountPool.dashboard.quotaRefresh")}</CardTitle>
            <Button type="button" variant="outline" size="sm" onClick={onRefreshQuotas} disabled={refreshingQuotas}>
              <RefreshCw className={refreshingQuotas || quotaRefreshStatus?.running ? "animate-spin" : undefined} />
              {t("accountPool.dashboard.refreshQuotas")}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
          <div className="rounded-md border p-3">
            <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.lastQuotaRefresh")}</p>
            <p className="mt-1 font-semibold">{formatDateTime(quotaRefreshStatus?.last_completed_at, i18n.language)}</p>
          </div>
          <div className="rounded-md border p-3">
            <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.nextQuotaRefresh")}</p>
            <p className="mt-1 font-semibold">{formatDateTime(quotaRefreshStatus?.next_refresh_at, i18n.language)}</p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <CardTitle className="text-base">{t("accountPool.dashboard.overview")}</CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.dashboard.overviewDescription")}</p>
            </div>
            <Badge variant="outline">
              <Radio className="mr-1 size-3" />
              {t("accountPool.dashboard.live")}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
          <div className="rounded-md border p-3">
            <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.requests")}</p>
            <p className="mt-1 font-semibold">{statsLoading ? "-" : metric(summary.totalRequests)}</p>
          </div>
          <div className="rounded-md border p-3">
            <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.successes")}</p>
            <p className="mt-1 font-semibold">{statsLoading ? "-" : metric(summary.successfulRequests)}</p>
          </div>
          <div className="rounded-md border p-3">
            <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.supplierFamilies")}</p>
            <p className="mt-1 font-semibold">{formatInteger(groups.length)}</p>
          </div>
          <div className="rounded-md border p-3">
            <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.health")}</p>
            <p className="mt-1 flex items-center gap-1 font-semibold">
              <Activity className="size-4 text-emerald-600" />
              {formatInteger(summary.enabledCards)} {t("accountPool.dashboard.available")}
            </p>
          </div>
        </CardContent>
      </Card>

      {environments.length > 0 && (
        <div className="grid gap-3">
          <div className="grid grid-cols-1 items-end gap-3 sm:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_10rem_11rem_8rem_auto]">
            <div className="grid gap-1.5">
              <Label htmlFor="dashboard-card-search">{t("accountPool.dashboard.cardSearch")}</Label>
              <Input id="dashboard-card-search" value={filters.search} placeholder={t("accountPool.dashboard.cardSearch")} onChange={(event) => updateFilter("search", event.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="dashboard-enabled-filter">{t("accountPool.dashboard.enabledFilter")}</Label>
              <Select value={filters.enabled} onValueChange={(value) => {
                if (value === "all" || value === "enabled" || value === "disabled") updateFilter("enabled", value);
              }}>
                <SelectTrigger id="dashboard-enabled-filter" aria-label={t("accountPool.dashboard.enabledFilter")} className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t("accountPool.dashboard.allEnabledStates")}</SelectItem>
                  <SelectItem value="enabled">{t("accountPool.dashboard.onlyEnabled")}</SelectItem>
                  <SelectItem value="disabled">{t("accountPool.dashboard.onlyDisabled")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="dashboard-status-filter">{t("accountPool.dashboard.runtimeStatusFilter")}</Label>
              <Select value={filters.status} onValueChange={(value) => {
                const status = STATUS_FILTERS.find((item) => item === value);
                if (status) updateFilter("status", status);
              }}>
                <SelectTrigger id="dashboard-status-filter" aria-label={t("accountPool.dashboard.runtimeStatusFilter")} className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {STATUS_FILTERS.map((status) => <SelectItem key={status} value={status}>{t(`accountPool.status.${status}`)}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="dashboard-page-size">{t("accountPool.dashboard.cardsPerPage")}</Label>
              <Select value={String(pageSize)} onValueChange={(value) => {
                const size = PAGE_SIZES.find((item) => String(item) === value);
                if (size) { setPageSize(size); setPages({}); }
              }}>
                <SelectTrigger id="dashboard-page-size" aria-label={t("accountPool.dashboard.cardsPerPage")} className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {PAGE_SIZES.map((size) => <SelectItem key={size} value={String(size)}>{size}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <Button type="button" variant="outline" onClick={clearFilters} disabled={!filters.search && filters.enabled === "all" && filters.status === "all"}>{t("accountPool.dashboard.clearFilters")}</Button>
          </div>
          <p className="text-sm text-muted-foreground">{t("accountPool.dashboard.filteredCards", { matched: filtered.length, total: environments.length })}</p>
        </div>
      )}

      {visibleGroups.length === 0 ? (
        <div className="rounded-md border border-dashed p-10 text-center text-sm text-muted-foreground">
          {t(environments.length === 0 ? "accountPool.dashboard.empty" : "accountPool.noMatchingEnvironments")}
        </div>
      ) : (
        visibleGroups.map((group) => (
          <section
            key={group.supplier}
            className="grid gap-3"
            aria-labelledby={`account-pool-family-${group.supplier}`}
          >
            <div className="flex items-center gap-2">
              <AccountPoolSupplierLogo supplier={group.supplier} />
              <h2 id={`account-pool-family-${group.supplier}`} className="text-base font-semibold">
                {t(`accountPool.supplier.${group.supplier}`)}
              </h2>
              <Badge variant="secondary">{group.environments.length}</Badge>
            </div>
            <AccountPoolSortableCards supplier={group.supplier} cards={group.environments} pagination={{ page: pages[group.supplier] ?? 1, pageSize, onPageChange: (page) => setPages((current) => ({ ...current, [group.supplier]: page })) }}>
              {(environment) => renderCard(environment, statsByCard.get(environment.id))}
            </AccountPoolSortableCards>
          </section>
        ))
      )}
    </div>
  );
};
