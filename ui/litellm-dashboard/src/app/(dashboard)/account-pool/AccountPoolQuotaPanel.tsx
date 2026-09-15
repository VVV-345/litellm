/** 本文件展示供应商返回的完整订阅、额度窗口和刷新诊断。 */

import { RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import {
  formatDateTime,
  formatQuota,
  formatQuotaAmounts,
  mostConstrainedWindow,
  quotaProxyLabel,
  quotaRows,
  quotaWindowLabel,
} from "./AccountPoolFormatters";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";

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
  const numberFormatter = new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 2 });

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
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {environments.map((environment) => {
            const constrained = mostConstrainedWindow(environment);
            const quota = environment.quota;
            const balances = quota.balances ?? [];
            const metadata = [
              quota.plan_type ? [t("accountPool.quotas.plan"), quota.plan_type] : null,
              quota.subscription_status
                ? [t("accountPool.quotas.subscriptionStatus"), quota.subscription_status]
                : null,
              quota.subscription_active_start
                ? [t("accountPool.quotas.activeFrom"), formatDateTime(quota.subscription_active_start, i18n.language)]
                : null,
              quota.subscription_active_until
                ? [t("accountPool.quotas.expiresAt"), formatDateTime(quota.subscription_active_until, i18n.language)]
                : null,
              quota.reset_credits_available != null
                ? [t("accountPool.quotas.resetCredits"), numberFormatter.format(quota.reset_credits_available)]
                : null,
              quota.prepaid_balance != null
                ? [
                    t("accountPool.quotas.prepaidBalance"),
                    `${numberFormatter.format(quota.prepaid_balance)} ${t("accountPool.quotas.unit.cents")}`,
                  ]
                : null,
              quota.extra_usage_enabled != null
                ? [
                    t("accountPool.quotas.extraUsageEnabled"),
                    quota.extra_usage_enabled ? t("accountPool.quotas.enabled") : t("accountPool.quotas.disabled"),
                  ]
                : null,
              quota.source
                ? [t("accountPool.quotas.sourceLabel"), t(`accountPool.quotas.source.${quota.source}`)]
                : null,
              [t("accountPool.quotas.proxyLabel"), quotaProxyLabel(t, environment)],
              [t("accountPool.quotas.updatedAtLabel"), formatDateTime(quota.observed_at, i18n.language)],
              quota.refresh_attempted_at
                ? [
                    t("accountPool.quotas.refreshAttemptedAtLabel"),
                    formatDateTime(quota.refresh_attempted_at, i18n.language),
                  ]
                : null,
            ].filter((item): item is string[] => item !== null);

            return (
              <Card key={environment.id}>
                <CardHeader className="pb-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <CardTitle className="truncate text-base">{environment.name}</CardTitle>
                    <div className="flex flex-wrap gap-2">
                      <Badge variant="outline">{t(`accountPool.supplier.${environment.supplier}`)}</Badge>
                      {quota.refresh_status ? (
                        <Badge
                          variant={
                            quota.refresh_status === "partial" || quota.refresh_status === "failed"
                              ? "destructive"
                              : "secondary"
                          }
                        >
                          {t(`accountPool.quotas.refreshStatus.${quota.refresh_status}`)}
                        </Badge>
                      ) : null}
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="grid gap-4 text-sm">
                  <div className="rounded-md border p-3">
                    <p className="text-xs text-muted-foreground">{t("accountPool.quotas.minimumRemaining")}</p>
                    <p className="mt-1 font-semibold">{formatQuota(t, constrained)}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {t("accountPool.quotas.reset", { time: formatDateTime(constrained?.resets_at, i18n.language) })}
                    </p>
                  </div>

                  {metadata.length > 0 ? (
                    <div className="grid gap-2 rounded-md border p-3 sm:grid-cols-2">
                      {metadata.map(([label, value]) => (
                        <div key={label}>
                          <p className="text-xs text-muted-foreground">{label}</p>
                          <p className="mt-1 break-words font-medium">{value}</p>
                        </div>
                      ))}
                    </div>
                  ) : null}

                  {quota.refresh_error ? (
                    <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100">
                      {quota.refresh_error}
                    </div>
                  ) : null}

                  {balances.length > 0 ? (
                    <div className="grid gap-2 rounded-md border p-3 sm:grid-cols-2">
                      {balances.map((balance) => (
                        <div key={balance.name}>
                          <p className="text-xs text-muted-foreground">
                            {t("accountPool.quotas.availableBalance", {
                              name: balance.name.replaceAll("_", " "),
                            })}
                          </p>
                          <p className="mt-1 font-medium">
                            {numberFormatter.format(balance.available)}
                            {balance.unit ? ` ${t(`accountPool.quotas.unit.${balance.unit}`, balance.unit)}` : ""}
                          </p>
                          {balance.minimum_required != null ? (
                            <p className="mt-1 text-xs text-muted-foreground">
                              {t("accountPool.quotas.minimumRequired", {
                                amount: numberFormatter.format(balance.minimum_required),
                              })}
                            </p>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  ) : null}

                  <div className="grid gap-3">
                    {quotaRows(t, environment).map((row) => (
                      <div key={row.key} className="rounded-md border p-3">
                        <p className="font-medium">{row.label}</p>
                        {row.quota.windows.length === 0 ? (
                          <p className="mt-2 text-xs text-muted-foreground">{t("accountPool.config.notObserved")}</p>
                        ) : (
                          <div className="mt-2 grid gap-3">
                            {row.quota.windows.map((window) => {
                              const amounts = formatQuotaAmounts(t, window, i18n.language);
                              return (
                                <div
                                  key={`${row.key}-${window.name}-${window.resets_at ?? "unknown"}`}
                                  className="grid gap-1 border-t pt-2 first:border-0 first:pt-0"
                                >
                                  <div className="flex items-center justify-between gap-3">
                                    <span className="truncate text-muted-foreground">
                                      {quotaWindowLabel(t, window.name)}
                                    </span>
                                    <span className="font-semibold">{formatQuota(t, window)}</span>
                                  </div>
                                  {amounts ? (
                                    <p className="text-xs text-muted-foreground">
                                      {t("accountPool.quotas.usedAmount", { amount: amounts })}
                                    </p>
                                  ) : null}
                                  <p className="text-xs text-muted-foreground">
                                    {t("accountPool.quotas.period", {
                                      start: formatDateTime(window.starts_at, i18n.language),
                                      end: formatDateTime(window.resets_at, i18n.language),
                                    })}
                                  </p>
                                </div>
                              );
                            })}
                          </div>
                        )}
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
