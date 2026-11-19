import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { accountPoolPlanLabel } from "../../utils/accountPoolCodexPlan";
import {
  displayedQuota,
  formatDateTime,
  formatQuota,
  quotaProxyLabel,
  quotaSnapshotIsStale,
  quotaWindowLabel,
} from "../../utils/AccountPoolFormatters";
import { accountPoolFamilyKind, type AccountPoolEnvironment } from "../../utils/AccountPoolTypes";
import { useAccountPoolClock } from "../../hooks/useAccountPoolClock";

export function AccountPoolCardQuota({
  environment,
  maxAgeSeconds = 300,
}: {
  environment: AccountPoolEnvironment;
  maxAgeSeconds?: number;
}) {
  const { t, i18n } = useTranslation();
  const now = useAccountPoolClock();
  const family = accountPoolFamilyKind(environment);
  const quota = displayedQuota(environment);
  const stale = quotaSnapshotIsStale(quota, now, maxAgeSeconds);
  const hasBalances = quota.balances.length > 0 || quota.prepaid_balance != null;
  const showResetCredits = environment.supplier === "openai_codex" || quota.reset_credits_available != null;
  const numberFormatter = new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 2 });
  const goQuota = family === "opencode" && (quota.plan_type === "OpenCode Go" || Boolean(quota.refresh_attempted_at));
  const isPlanQuota = family === "glm" || goQuota;
  const billingLink =
    family === "opencode" ? (
      <div className="grid gap-1 text-xs">
        <a
          className="w-fit font-medium underline underline-offset-4"
          href="https://opencode.ai/console/login"
          target="_blank"
          rel="noopener noreferrer"
        >
          {t("accountPool.quotas.openCodeBilling")}
        </a>
        <span className="text-muted-foreground">{t("accountPool.quotas.openCodeBillingHint")}</span>
      </div>
    ) : null;
  if (family === "opencode" && !goQuota) {
    return (
      <div className="grid gap-1 rounded-lg border bg-muted/20 p-2.5 text-xs">
        <span className="text-muted-foreground">{t("accountPool.quotas.workspaceNotFetched")}</span>
        {billingLink}
      </div>
    );
  }
  const plan =
    environment.supplier === "openai_codex"
      ? accountPoolPlanLabel(quota.plan_type, quota.auth_file_plan_type)
      : quota.plan_type || "-";
  const planLabel = plan === "PLUS" ? "Plus" : plan;
  const proxyLabel = quotaProxyLabel(t, environment);
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
    <section
      className="@container/quota grid gap-2 rounded-lg border bg-muted/20 p-2.5"
      aria-label={t("accountPool.quotas.title")}
    >
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge variant="outline">
          <span className="text-muted-foreground">{t("accountPool.quotas.plan")}</span>
          <span>{planLabel}</span>
        </Badge>
        {isPlanQuota && <span className="text-muted-foreground">{t("accountPool.quotas.codingPlanQuota")}</span>}
        {!isPlanQuota && showResetCredits && (
          <span className="tabular-nums text-muted-foreground">
            {t("accountPool.quotas.availableResets", {
              countText: quota.reset_credits_available ?? t("accountPool.dashboard.unknown"),
            })}
          </span>
        )}
        {stale && <Badge variant="outline">{t("accountPool.quotas.staleSnapshot")}</Badge>}
        {quota.source && <Badge variant="secondary">{t(`accountPool.quotas.source.${quota.source}`)}</Badge>}
        {quota.refresh_status && (
          <Badge variant={quota.refresh_status === "failed" ? "destructive" : "secondary"}>
            {t(`accountPool.quotas.refreshStatus.${quota.refresh_status}`)}
          </Badge>
        )}
      </div>
      {quota.windows.length === 0 && !hasBalances && (
        <p className="text-xs text-muted-foreground">
          {t(isPlanQuota ? "accountPool.quotas.codingPlanNotFetched" : "accountPool.config.notObserved")}
        </p>
      )}
      {hasBalances && (
        <div className="grid gap-2 text-xs @sm/quota:grid-cols-2">
          {quota.prepaid_balance != null && (
            <div>
              <p className="text-muted-foreground">{t("accountPool.quotas.prepaidBalance")}</p>
              <p className="font-medium tabular-nums">
                {numberFormatter.format(quota.prepaid_balance)} {t("accountPool.quotas.unit.cents")}
              </p>
            </div>
          )}
          {quota.balances.map((balance) => (
            <div key={balance.name}>
              <p className="text-muted-foreground">
                {t("accountPool.quotas.availableBalance", { name: balance.name.replaceAll("_", " ") })}
              </p>
              <p className="font-medium tabular-nums">
                {numberFormatter.format(balance.available)}
                {balance.unit ? ` ${t(`accountPool.quotas.unit.${balance.unit}`, balance.unit)}` : ""}
              </p>
              {balance.minimum_required != null && (
                <p className="text-muted-foreground">
                  {t("accountPool.quotas.minimumRequired", {
                    amount: numberFormatter.format(balance.minimum_required),
                  })}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
      <div className="grid gap-2 @sm/quota:grid-cols-2">
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
      </div>
      <div className="grid gap-1 border-t pt-2 text-[11px] text-muted-foreground sm:grid-cols-2">
        <span>{t("accountPool.quotas.proxy", { proxy: proxyLabel })}</span>
        <span>{t("accountPool.quotas.updatedAt", { time: formatDateTime(quota.observed_at, i18n.language) })}</span>
        {quota.refresh_attempted_at && (
          <span className="sm:col-span-2">
            {t("accountPool.quotas.refreshAttemptedAt", {
              time: formatDateTime(quota.refresh_attempted_at, i18n.language),
            })}
          </span>
        )}
      </div>
      {quota.refresh_error && (
        <p role="status" className="break-words text-xs text-destructive">
          {quota.refresh_error}
        </p>
      )}
      {billingLink}
      {(environment.model_cooldowns ?? []).map((cooldown) => (
        <p key={cooldown.model} role="status" className="break-words text-xs text-amber-700 dark:text-amber-400">
          {t("accountPool.quotas.modelCooldown", {
            model: cooldown.model,
            time: formatDateTime(cooldown.retry_at, i18n.language),
          })}
        </p>
      ))}
    </section>
  );
}
