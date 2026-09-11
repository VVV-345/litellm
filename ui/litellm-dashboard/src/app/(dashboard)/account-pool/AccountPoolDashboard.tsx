/** 本文件渲染号池仪表盘的汇总指标和供应商分组，卡片操作由页面注入以保持权限边界。 */

import type { ReactNode } from "react";
import { Activity, AlertTriangle, Gauge, Layers3, Radio, TrafficCone } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import type { ErrorStats } from "./AccountPoolManagementApi";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";
import { groupAccountPoolEnvironments, summarizeAccountPoolDashboard } from "./accountPoolDashboardSelectors";

interface AccountPoolDashboardProps {
  environments: readonly AccountPoolEnvironment[];
  statsByCard: ReadonlyMap<string, ErrorStats>;
  statsLoading: boolean;
  renderCard: (environment: AccountPoolEnvironment, stats: ErrorStats | undefined) => ReactNode;
}

const formatInteger = (value: number): string => new Intl.NumberFormat("zh-CN").format(value);

export const AccountPoolDashboard = ({
  environments,
  statsByCard,
  statsLoading,
  renderCard,
}: AccountPoolDashboardProps) => {
  const { t } = useTranslation();
  const summary = summarizeAccountPoolDashboard(environments, statsByCard);
  const groups = groupAccountPoolEnvironments(environments);
  const successRate =
    summary.successRate === null ? t("accountPool.dashboard.unknown") : `${summary.successRate.toFixed(1)}%`;

  return (
    <div className="grid gap-5" data-testid="account-pool-dashboard">
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
                {statsLoading ? <Skeleton className="h-7 w-16" /> : formatInteger(summary.failedRequests)}
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
                {statsLoading ? <Skeleton className="h-7 w-16" /> : formatInteger(summary.totalTokens)}
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
      </div>

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
            <p className="mt-1 font-semibold">{statsLoading ? "-" : formatInteger(summary.totalRequests)}</p>
          </div>
          <div className="rounded-md border p-3">
            <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.successes")}</p>
            <p className="mt-1 font-semibold">{statsLoading ? "-" : formatInteger(summary.successfulRequests)}</p>
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

      {groups.length === 0 ? (
        <div className="rounded-md border border-dashed p-10 text-center text-sm text-muted-foreground">
          {t("accountPool.dashboard.empty")}
        </div>
      ) : (
        groups.map((group) => (
          <section
            key={group.supplier}
            className="grid gap-3"
            aria-labelledby={`account-pool-family-${group.supplier}`}
          >
            <div className="flex items-center gap-2">
              <h2 id={`account-pool-family-${group.supplier}`} className="text-base font-semibold">
                {t(`accountPool.supplier.${group.supplier}`)}
              </h2>
              <Badge variant="secondary">{group.environments.length}</Badge>
            </div>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {group.environments.map((environment) => renderCard(environment, statsByCard.get(environment.id)))}
            </div>
          </section>
        ))
      )}
    </div>
  );
};
