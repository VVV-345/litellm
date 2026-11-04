/** 本文件渲染单个号池环境卡片，负责展示状态与触发页面级操作。 */

import { PlugZap, KeyRound, Trash2, Settings2 } from "lucide-react";
import Link from "next/link";
import { memo } from "react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { pageHref } from "@/utils/migratedPages";

import {
  canAuthorizeEnvironment,
  canConfigureEnvironment,
  canDeleteEnvironment,
  canToggleEnvironment,
} from "../../utils/AccountPoolPermissions";
import {
  formatCount,
  formatDateTime,
  formatQuota,
  mostConstrainedWindow,
  statusLabel,
  statusVariant,
} from "../../utils/AccountPoolFormatters";
import type { ErrorStats } from "../../api/AccountPoolManagementApi";
import type { PolicyView } from "../../api/AccountPoolManagementApi";
import type {
  AccountPoolEnvironment,
  AccountPoolProxyGateway,
  AccountPoolProxyGatewayDelay,
  AccountPoolISPProbe,
} from "../../utils/AccountPoolTypes";
import { accountPoolLatency } from "../../utils/accountPoolLatency";
import { AccountPoolCardQuota } from "./AccountPoolCardQuota";
import { AccountPoolSupplierLogo } from "../providers/AccountPoolSupplierLogo";
import { accountPoolHiddenModelCount, accountPoolUsableModels, accountPoolVisibleModels } from "../../utils/accountPoolDashboardSelectors";

interface AccountPoolCardProps {
  environment: AccountPoolEnvironment;
  proxyGateway?: AccountPoolProxyGateway;
  proxyName?: string;
  proxyDelay?: AccountPoolProxyGatewayDelay;
  ispProbe?: AccountPoolISPProbe | null;
  onConfigure: (environment: AccountPoolEnvironment) => void;
  onEnabledChange: (environment: AccountPoolEnvironment, enabled: boolean) => void;
  onAuthorize: (environment: AccountPoolEnvironment) => void;
  onDelete: (environment: AccountPoolEnvironment) => void;
  onManageKey: (environment: AccountPoolEnvironment) => void;
  onTest?: (environment: AccountPoolEnvironment) => void;
  onQuotaReset?: (environment: AccountPoolEnvironment) => void;
  onRefreshCredentials?: (environment: AccountPoolEnvironment) => void;
  onVerifyAccess?: (environment: AccountPoolEnvironment) => void;
  onViewLogs?: (environment: AccountPoolEnvironment) => void;
  requestStats?: ErrorStats;
  compactNumbers?: boolean;
  tags?: string[];
  group?: string;
  policy?: PolicyView;
  disabled?: boolean;
}

const AccountPoolCardComponent = ({
  environment,
  proxyGateway,
  proxyName,
  proxyDelay,
  ispProbe,
  onConfigure,
  onEnabledChange,
  onAuthorize,
  onDelete,
  onManageKey,
  onViewLogs,
  onTest,
  onQuotaReset,
  onRefreshCredentials,
  onVerifyAccess,
  requestStats,
  compactNumbers = true,
  tags = [],
  group,
  policy,
  disabled = false,
}: AccountPoolCardProps) => {
  const { t, i18n } = useTranslation();
  const latency = accountPoolLatency(proxyGateway, proxyDelay, ispProbe);
  const quotaWindow = mostConstrainedWindow(environment);
  const configuredPort = environment.proxy_profile_id?.match(/^clash-gateway-(\d+)$/)?.[1];
  const proxyPort = proxyGateway?.port ?? configuredPort;
  const proxyLabel = proxyPort
    ? t("accountPool.proxyGateways.assignment", {
        port: proxyPort,
        node: proxyGateway?.current_node ?? t("accountPool.proxyGateways.currentNodeUnknown"),
      })
    : environment.proxy_profile_id;
  const authorizationAction =
    environment.status === "error" || environment.access_token_fallback?.failure_source === "refresh"
      ? t("accountPool.reauthorize")
      : t("accountPool.continueAuthorization");
  const usableModels = accountPoolUsableModels(environment);
  const visibleModels = accountPoolVisibleModels(environment);
  const hiddenModelCount = accountPoolHiddenModelCount(environment);
  const showPlan = environment.supplier !== "openai_codex" && Boolean(environment.quota.plan_type);
  const showLabels = showPlan || Boolean(group) || tags.length > 0;
  const policyValues = policy?.policy;
  const completedRequests = (requestStats?.succeeded_requests ?? 0) + (requestStats?.failed_requests ?? 0);
  const successRate =
    completedRequests === 0 ? null : ((requestStats?.succeeded_requests ?? 0) / completedRequests) * 100;
  const providerSummary = (() => {
    if (environment.supplier === "kimi" && !policyValues?.kimi) {
      return t("accountPool.policy.kimiDeviceIdAuto");
    }
    if (!policyValues) return null;
    if (environment.supplier === "openai_codex" && policyValues.codex) {
      const enabled = [
        policyValues.codex.identity_fingerprint_mode !== "off" &&
          `${t("accountPool.policy.identity_fingerprint_mode")}: ${t(
            `accountPool.policy.options.${policyValues.codex.identity_fingerprint_mode}`,
          )}`,
        policyValues.codex.cli_only && t("accountPool.policy.cli_only"),
        policyValues.codex.allow_app_server && t("accountPool.policy.allow_app_server"),
        policyValues.codex.identity_confuse && t("accountPool.policy.identity_confuse"),
        policyValues.codex.disable_codex_cloaking && t("accountPool.policy.disable_codex_cloaking"),
      ].filter(Boolean);
      return enabled.length > 0 ? enabled.join(" / ") : t("accountPool.policy.options.disabled");
    }
    if (environment.supplier === "anthropic_claude" && policyValues.claude) {
      return `${t(`accountPool.policy.options.${policyValues.claude.fingerprint_profile}`)} / ${t(
        `accountPool.policy.options.${policyValues.claude.cloak_mode}`,
      )}`;
    }
    if (environment.supplier === "kimi") {
      if (!policyValues.kimi) return null;
      return `${t(`accountPool.policy.options.${policyValues.kimi.fingerprint_profile}`)} / ${t(
        "accountPool.policy.kimiDeviceIdAuto",
      )}`;
    }
    if (environment.supplier === "xai" && policyValues.xai) {
      return t(`accountPool.policy.options.${policyValues.xai.inject_x_search ? "enabled" : "disabled"}`);
    }
    if (environment.supplier === "google_antigravity" && policyValues.antigravity) {
      return `${t("accountPool.policy.sensitiveWordCount", {
        count: policyValues.antigravity.sensitive_words.length,
      })} / ${t(
        `accountPool.policy.options.${policyValues.antigravity.signature_cache_enabled ? "enabled" : "disabled"}`,
      )}`;
    }
    return null;
  })();
  const healthLabel = (() => {
    if (environment.status === "ready") return t("accountPool.dashboard.healthy");
    if (environment.status === "error") return t("accountPool.dashboard.unhealthy");
    return t("accountPool.dashboard.checking");
  })();
  const spendUsd = (requestStats?.known_cost_requests ?? 0) > 0 ? requestStats?.total_cost_usd : null;
  const spendAmount =
    spendUsd == null
      ? t("accountPool.stats.costUnavailable")
      : `$${
          spendUsd > 0 && spendUsd < 0.01
            ? spendUsd.toLocaleString("en-US", { maximumSignificantDigits: 6 })
            : spendUsd.toFixed(2)
        }`;
  return (
    <Card
      className="@container/card min-w-0 gap-3 [--card-spacing:--spacing(4)]"
      data-testid={`account-pool-card-${environment.id}`}
    >
      <CardHeader className="gap-2">
        <div className="flex min-w-0 items-start justify-between gap-3 pr-8">
          <div className="flex min-w-0 items-start gap-3">
            <AccountPoolSupplierLogo supplier={environment.supplier} className="mt-0.5 size-6 shrink-0" />
            <div className="min-w-0">
              <CardTitle className="break-all text-base leading-6">{environment.name}</CardTitle>
              <p className="mt-1 text-xs text-muted-foreground">
                {t(`accountPool.channel.${environment.channel}`)} · {t(`accountPool.supplier.${environment.supplier}`)}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {t("accountPool.spendUsed", { amount: spendAmount })}
              </p>
              {environment.configuration_pending && (
                <p className="mt-1 text-xs text-muted-foreground" role="status">
                  {t("accountPool.configurationSyncing")}
                </p>
              )}
              {environment.status === "awaiting_authorization" && (
                <p className="mt-1 text-xs text-muted-foreground" role="status">
                  {t("accountPool.preAuthorizationProxyHint")}
                </p>
              )}
            </div>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1">
            <Badge variant={statusVariant(environment.status)}>{statusLabel(t, environment.status)}</Badge>
            <Link href={pageHref("policy-center")} className="text-xs text-muted-foreground underline">
              {(policy?.version ?? 0) > 0
                ? t("accountPool.policy.independentBadge", { version: policy?.version })
                : t("accountPool.policy.inheritedBadge")}
            </Link>
          </div>
        </div>
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <span>{t("accountPool.enabled")}</span>
            <Switch
              checked={environment.enabled}
              onCheckedChange={(checked) => onEnabledChange(environment, checked === true)}
              disabled={disabled || !canToggleEnvironment(environment)}
              aria-label={t("accountPool.enableEnvironment", { name: environment.name })}
            />
          </div>
          <div className="flex flex-wrap justify-end gap-1">
            {onTest && (
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                onClick={() => onTest(environment)}
                disabled={disabled || !environment.enabled || usableModels.length === 0}
                aria-label={t("accountPool.modelTest.title", { name: environment.name })}
                title={t("accountPool.modelTest.short")}
              >
                <PlugZap />
              </Button>
            )}
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => onManageKey(environment)}
              disabled={disabled}
              aria-label={t("accountPool.keys.manage", { name: environment.name })}
              title={t("accountPool.keys.manageShort")}
            >
              <KeyRound />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => onConfigure(environment)}
              disabled={disabled || !canConfigureEnvironment(environment)}
              aria-label={t("accountPool.configureEnvironment", { name: environment.name })}
              title={t("accountPool.configure")}
            >
              <Settings2 />
            </Button>
            {(canAuthorizeEnvironment(environment) ||
              environment.access_token_fallback?.failure_source === "refresh") && (
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                onClick={() => onAuthorize(environment)}
                disabled={disabled}
                aria-label={t("accountPool.continueAuthorizationFor", {
                  action: authorizationAction,
                  name: environment.name,
                })}
                title={authorizationAction}
              >
                <KeyRound />
              </Button>
            )}
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => onDelete(environment)}
              disabled={disabled || !canDeleteEnvironment(environment)}
              aria-label={t("accountPool.deleteEnvironment", { name: environment.name })}
              title={t("accountPool.delete")}
            >
              <Trash2 />
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm">
        {environment.supplier === "openai_codex" && <AccountPoolCardQuota environment={environment} />}
        {environment.access_token_fallback?.active && (
          <p role="status" className="text-sm font-medium text-amber-700 dark:text-amber-400">
            {t("accountPool.credential.temporary")}
          </p>
        )}
        {environment.access_token_fallback?.enabled && environment.access_token_fallback.failure_source === "refresh" && (
          <p className="text-xs text-muted-foreground">仅显示验证通过的模型；后台自动检查，失败模型每 5 分钟重试，独立冷却不会解除</p>
        )}
        {environment.credential_refresh?.access_expires_at && (
          <p className="text-xs text-muted-foreground">
            {t("accountPool.credential.expires")}:{" "}
            {formatDateTime(environment.credential_refresh.access_expires_at, i18n.language)}
          </p>
        )}
        {environment.credential_refresh &&
          ["failed", "rejected", "save_pending"].includes(environment.credential_refresh.last_result ?? "") && (
            <p role="status" className="text-xs text-destructive">
              {t(`accountPool.credential.result.${environment.credential_refresh.last_result}`)}
            </p>
          )}
        {environment.last_error && <p className="break-words text-xs text-destructive">{environment.last_error}</p>}
        {environment.supplier === "openai_codex" && (
          <div className="flex flex-wrap gap-2">
            {onRefreshCredentials && (
              <Button
                size="sm"
                variant="outline"
                disabled={disabled || !environment.enabled || environment.credential_refresh?.running}
                onClick={() => onRefreshCredentials(environment)}
              >
                {t("accountPool.credential.refresh")}
              </Button>
            )}
            {onVerifyAccess && policyValues?.codex?.allow_valid_access_after_refresh_failure && (
              <Button
                size="sm"
                variant="outline"
                disabled={disabled || !environment.enabled}
                onClick={() => onVerifyAccess(environment)}
              >
                {t("accountPool.credential.verify")}
              </Button>
            )}
          </div>
        )}
            <div className="min-w-0">
              <p className="text-xs text-muted-foreground">{t("accountPool.config.outboundProxy")}</p>
              <p className="mt-1 break-words font-medium">
                {proxyLabel && proxyPort ? proxyLabel : proxyName || proxyLabel || t("accountPool.config.defaultGateway")}
              </p>
            </div>
            <div
              className="flex items-center justify-between gap-2 text-xs"
              title={latency.checkedAt ? formatDateTime(latency.checkedAt, i18n.language) : undefined}
            >
              <span className="text-muted-foreground">{t("accountPool.nodeLatency.label")}</span>
              <span className="font-medium tabular-nums">
                {latency.status === "ok" ? `${latency.delay} ms` : t(`accountPool.nodeLatency.${latency.status}`)}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-2 @sm/card:grid-cols-4">
              <div><p className="text-xs text-muted-foreground">失败请求</p><p className="mt-1 font-medium">{requestStats ? formatCount(requestStats.failed_requests, compactNumbers) : "-"}</p></div>
              <div><p className="text-xs text-muted-foreground">Tokens</p><p className="mt-1 font-medium">{requestStats ? formatCount(requestStats.input_tokens + requestStats.output_tokens, compactNumbers) : "-"}</p></div>
              <div>
                <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.requests")}</p>
                <p className="mt-1 font-medium">{requestStats ? formatCount(requestStats.total_requests, compactNumbers) : "-"}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">{t("accountPool.config.concurrencyLimit")}</p>
                <p className="mt-1 font-medium">
                  {environment.concurrency_limit === 0
                    ? t("accountPool.config.unlimited")
                    : environment.concurrency_limit}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.cardSuccessRate")}</p>
                <p className="mt-1 font-medium">{successRate === null ? "-" : `${successRate.toFixed(1)}%`}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.health")}</p>
                <p className="mt-1 font-medium">{healthLabel}</p>
              </div>
              {environment.supplier !== "openai_codex" && (
                <>
                  <div>
                    <p className="text-xs text-muted-foreground">{t("accountPool.remainingQuota")}</p>
                    <p className="mt-1 font-medium">{formatQuota(t, quotaWindow)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">{t("accountPool.nextReset")}</p>
                    <p className="mt-1 font-medium">{formatDateTime(quotaWindow?.resets_at, i18n.language)}</p>
                  </div>
                </>
              )}
              {environment.supplier === "xai" &&
                environment.quota.has_grok_code_access !== null &&
                environment.quota.has_grok_code_access !== undefined && (
                  <div>
                    <p className="text-xs text-muted-foreground">{t("accountPool.grokCodeAccess")}</p>
                    <p className="mt-1 font-medium">
                      {t(environment.quota.has_grok_code_access ? "accountPool.available" : "accountPool.unavailable")}
                    </p>
                  </div>
                )}
            </div>
        <div className="flex flex-wrap gap-2">
          {environment.supplier === "openai_codex" && onQuotaReset && (
            <Button size="sm" variant="outline" disabled={disabled} onClick={() => onQuotaReset(environment)}>
              {t("accountPool.quotaReset.action")}
            </Button>
          )}
          {onViewLogs && (
            <Button variant="outline" size="sm" onClick={() => onViewLogs(environment)}>
              {t("accountPool.logs.cardLogs")}
            </Button>
          )}
        </div>
        <details className="min-w-0 border-t pt-2">
          <summary className="cursor-pointer text-xs text-muted-foreground focus-visible:outline-2">
            {t("accountPool.credential.details")}
          </summary>
          <div className="mt-3 grid min-w-0 gap-3">
            <p className="break-all text-xs text-muted-foreground">ID: {environment.id}</p>
            <p className="text-xs text-muted-foreground">
              {t("accountPool.config.versions", {
                desired: environment.desired_configuration_version ?? 0,
                observed: environment.observed_configuration_version ?? 0,
              })}
            </p>
            {environment.credential_refresh && (
              <dl className="grid gap-1 text-xs">
                <dt>{t("accountPool.credential.next")}</dt>
                <dd>
                  {t(
                    `accountPool.credential.schedule.${environment.credential_refresh.schedule_reason ?? "unavailable"}`,
                  )}
                  {environment.credential_refresh.next_refresh_at &&
                    ` · ${formatDateTime(environment.credential_refresh.next_refresh_at, i18n.language)}`}
                </dd>
                <dt>{t("accountPool.credential.last")}</dt>
                <dd>
                  {t(`accountPool.credential.result.${environment.credential_refresh.last_result ?? "unknown"}`)} ·{" "}
                  {formatDateTime(
                    environment.credential_refresh.last_attempt_at ?? environment.credential_refresh.last_success_at,
                    i18n.language,
                  )}
                </dd>
              </dl>
            )}
            {showLabels && (
              <div className="flex flex-wrap gap-1.5">
                {showPlan && <Badge variant="outline">{environment.quota.plan_type}</Badge>}
                {group && <Badge variant="secondary">{group}</Badge>}
                {tags.map((tag) => (
                  <Badge key={tag} variant="outline">
                    {tag}
                  </Badge>
                ))}
              </div>
            )}
            <p className="border-t border-border pt-2 text-xs text-muted-foreground">
              路由策略、权重和优先级在 LiteLLM 模型设置中管理
            </p>
            {providerSummary && (
              <div className="grid gap-1 border-t border-border pt-2">
                <p className="text-xs text-muted-foreground">{t("accountPool.policy.providerSummary")}</p>
                <p className="font-medium">{providerSummary}</p>
              </div>
            )}
            <div>
              <p className="text-xs text-muted-foreground">{t("accountPool.availableModels")}</p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {usableModels.length > 0 ? (
                  <>
                    {visibleModels.map((model) => (
                      <Badge key={model} variant="outline">
                        {model}
                      </Badge>
                    ))}
                    {hiddenModelCount > 0 && (
                      <Badge
                        variant="secondary"
                        title={usableModels.slice(3).join("\n")}
                        aria-label={t("accountPool.dashboard.moreModels", { count: hiddenModelCount })}
                      >
                        +{hiddenModelCount}
                      </Badge>
                    )}
                  </>
                ) : (
                  <span className="text-muted-foreground">{t("accountPool.noEnabledModels")}</span>
                )}
              </div>
            </div>

          </div>
        </details>
      </CardContent>
    </Card>
  );
};

export const AccountPoolCard = memo(AccountPoolCardComponent);
