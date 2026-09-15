import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { accountPoolPlanLabel } from "./accountPoolCodexPlan";
import { formatDateTime, formatQuota, quotaWindowLabel } from "./AccountPoolFormatters";
import type { AccountPoolQuotaSnapshot } from "./AccountPoolTypes";

export function AccountPoolCardQuota({ quota }: { quota: AccountPoolQuotaSnapshot }) {
  const { t, i18n } = useTranslation();
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const plan = accountPoolPlanLabel(quota.plan_type, quota.auth_file_plan_type);
  const planLabel = plan === "PLUS" ? "Plus" : plan;
  const resetDistance = (value: string | null | undefined) => {
    if (!value) return null;
    const difference = new Date(value).getTime() - now;
    if (!Number.isFinite(difference)) return null;
    if (difference <= 0) return t("accountPool.quotas.awaitingRefresh");
    const formatter = new Intl.RelativeTimeFormat(i18n.language, { numeric: "always" });
    if (difference >= 86_400_000) return formatter.format(Math.ceil(difference / 86_400_000), "day");
    if (difference >= 3_600_000) return formatter.format(Math.ceil(difference / 3_600_000), "hour");
    return formatter.format(Math.ceil(difference / 60_000), "minute");
  };

  return (
    <section className="grid gap-3 rounded-lg border bg-muted/20 p-3" aria-label={t("accountPool.quotas.title")}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge variant="outline">
          <span className="text-muted-foreground">{t("accountPool.quotas.plan")}</span>
          <span>{planLabel}</span>
        </Badge>
        <span className="tabular-nums text-muted-foreground">
          {t("accountPool.quotas.availableResets", {
            countText: quota.reset_credits_available ?? t("accountPool.dashboard.unknown"),
          })}
        </span>
      </div>
      {quota.windows.length === 0 && (
        <p className="text-xs text-muted-foreground">{t("accountPool.config.notObserved")}</p>
      )}
      {quota.windows.map((window, index) => {
        const label = quotaWindowLabel(t, window.name);
        const distance = resetDistance(window.resets_at);
        return (
          <div key={`${window.name}-${index}`} role="group" aria-label={label} className="grid gap-1.5">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-xs">
              <span className="min-w-0 break-words font-medium">{label}</span>
              <span className="font-semibold tabular-nums">{formatQuota(t, window)}</span>
            </div>
            <div
              role="meter"
              aria-label={label}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={window.remaining_percent}
              aria-valuetext={t("accountPool.quotas.remainingValue", { percent: formatQuota(t, window) })}
              title={t("accountPool.quotas.remainingValue", { percent: formatQuota(t, window) })}
              className="h-1.5 overflow-hidden rounded-full bg-emerald-600/15 forced-colors:border"
            >
              <div
                className="h-full rounded-full bg-emerald-600 dark:bg-emerald-400 forced-colors:bg-[Highlight]"
                style={{ width: `${window.remaining_percent}%` }}
              />
            </div>
            <p className="flex flex-wrap justify-between gap-x-2 text-[11px] tabular-nums text-muted-foreground">
              <span>{t("accountPool.quotas.reset", { time: formatDateTime(window.resets_at, i18n.language) })}</span>
              {distance && <span>{distance}</span>}
            </p>
          </div>
        );
      })}
      <div className="border-t pt-2 text-[11px] text-muted-foreground">
        {t("accountPool.quotas.updatedAt", { time: formatDateTime(quota.observed_at, i18n.language) })}
      </div>
      {quota.refresh_error && (
        <p role="status" className="break-words text-xs text-destructive">
          {quota.refresh_error}
        </p>
      )}
    </section>
  );
}
