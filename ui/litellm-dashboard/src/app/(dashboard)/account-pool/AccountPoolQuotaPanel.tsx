/** 本文件按卡片和模型展示已保存的配额快照，刷新动作由页面注入。 */

import { RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { formatDateTime, formatQuota, mostConstrainedWindow, quotaRows } from "./AccountPoolFormatters";
import type { AccountPoolEnvironment, AccountPoolQuotaWindow } from "./AccountPoolTypes";

const lowestWindow = (windows: readonly AccountPoolQuotaWindow[]): AccountPoolQuotaWindow | null =>
  windows.reduce<AccountPoolQuotaWindow | null>(
    (lowest, window) => (lowest === null || window.remaining_percent < lowest.remaining_percent ? window : lowest),
    null,
  );

export const AccountPoolQuotaPanel = ({
  environments,
  onRefresh,
  refreshing = false,
}: {
  environments: readonly AccountPoolEnvironment[];
  onRefresh: () => void;
  refreshing?: boolean;
}) => {
  const { t, i18n } = useTranslation();
  return (
    <div className="grid gap-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">{t("accountPool.quotas.title")}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.quotas.description")}</p>
        </div>
        <Button type="button" variant="outline" size="sm" onClick={onRefresh} disabled={refreshing}>
          <RefreshCw className={refreshing ? "animate-spin" : undefined} />
          {t("accountPool.quotas.refresh")}
        </Button>
      </div>
      {environments.length === 0 ? (
        <div className="rounded-md border border-dashed p-10 text-center text-sm text-muted-foreground">
          {t("accountPool.dashboard.empty")}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {environments.map((environment) => {
            const constrained = mostConstrainedWindow(environment);
            return (
              <Card key={environment.id}>
                <CardHeader className="pb-3">
                  <div className="flex items-center justify-between gap-3">
                    <CardTitle className="truncate text-base">{environment.name}</CardTitle>
                    <Badge variant="outline">{t(`accountPool.supplier.${environment.supplier}`)}</Badge>
                  </div>
                </CardHeader>
                <CardContent className="grid gap-3 text-sm">
                  <div className="rounded-md border p-3">
                    <p className="text-xs text-muted-foreground">{t("accountPool.quotas.minimumRemaining")}</p>
                    <p className="mt-1 font-semibold">{formatQuota(t, constrained)}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {t("accountPool.quotas.reset", { time: formatDateTime(constrained?.resets_at, i18n.language) })}
                    </p>
                  </div>
                  <div className="grid gap-2">
                    {quotaRows(t, environment).map((row) => (
                      <div key={row.key} className="flex items-center justify-between gap-3">
                        <span className="truncate text-muted-foreground">{row.label}</span>
                        <span className="font-medium">{formatQuota(t, lowestWindow(row.quota.windows))}</span>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
};
