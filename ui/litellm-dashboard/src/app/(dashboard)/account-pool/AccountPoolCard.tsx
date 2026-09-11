/** 本文件渲染单个号池环境卡片，负责展示状态与触发页面级操作。 */

import { KeyRound, Trash2, Settings2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";

import {
  canAuthorizeEnvironment,
  canConfigureEnvironment,
  canDeleteEnvironment,
  canToggleEnvironment,
} from "./AccountPoolPermissions";
import {
  formatDateTime,
  formatQuota,
  mostConstrainedWindow,
  statusLabel,
  statusVariant,
} from "./AccountPoolFormatters";
import type { ErrorStats } from "./AccountPoolManagementApi";
import type { PolicyView } from "./AccountPoolManagementApi";
import type { AccountPoolEnvironment, AccountPoolProxyGateway } from "./AccountPoolTypes";
import { accountPoolHiddenModelCount, accountPoolVisibleModels } from "./accountPoolDashboardSelectors";

interface AccountPoolCardProps {
  environment: AccountPoolEnvironment;
  proxyGateway?: AccountPoolProxyGateway;
  onConfigure: (environment: AccountPoolEnvironment) => void;
  onEnabledChange: (environment: AccountPoolEnvironment, enabled: boolean) => void;
  onAuthorize: (environment: AccountPoolEnvironment) => void;
  onDelete: (environment: AccountPoolEnvironment) => void;
  onManageKey: (environment: AccountPoolEnvironment) => void;
  onManagePolicy: (environment: AccountPoolEnvironment) => void;
  onViewLogs?: (environment: AccountPoolEnvironment) => void;
  requestStats?: ErrorStats;
  tags?: string[];
  group?: string;
  policy?: PolicyView;
  disabled?: boolean;
}

export const AccountPoolCard = ({
  environment,
  proxyGateway,
  onConfigure,
  onEnabledChange,
  onAuthorize,
  onDelete,
  onManageKey,
  onManagePolicy,
  onViewLogs,
  requestStats,
  tags = [],
  group,
  policy,
  disabled = false,
}: AccountPoolCardProps) => {
  const { t, i18n } = useTranslation();
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
    environment.status === "error" ? t("accountPool.reauthorize") : t("accountPool.continueAuthorization");
  const visibleModels = accountPoolVisibleModels(environment);
  const hiddenModelCount = accountPoolHiddenModelCount(environment);
  const policyValues = policy?.policy;
  const completedRequests = (requestStats?.succeeded_requests ?? 0) + (requestStats?.failed_requests ?? 0);
  const successRate =
    completedRequests === 0 ? null : ((requestStats?.succeeded_requests ?? 0) / completedRequests) * 100;
  const providerSummary = (() => {
    if (environment.supplier === "kimi" && environment.status === "ready") {
      return t("accountPool.policy.kimiDeviceIdAuto");
    }
    if (!policyValues) return null;
    if (environment.supplier === "anthropic_claude" && policyValues.claude) {
      return t(`accountPool.policy.options.${policyValues.claude.fingerprint_profile}`);
    }
    if (environment.supplier === "xai" && policyValues.xai) {
      return t(`accountPool.policy.options.${policyValues.xai.inject_x_search ? "enabled" : "disabled"}`);
    }
    if (environment.supplier === "openai_compatible" && policyValues.openai_compatible) {
      return t(
        `accountPool.policy.options.${policyValues.openai_compatible.support_prompt_cache_key ? "enabled" : "disabled"}`,
      );
    }
    if (environment.supplier === "google_antigravity" && policyValues.antigravity) {
      return `${t(`accountPool.policy.options.${policyValues.antigravity.sensitive_word_filter}`)} / ${t(
        `accountPool.policy.options.${policyValues.antigravity.signature_cache}`,
      )}`;
    }
    return null;
  })();
  const healthLabel = (() => {
    if (environment.status === "ready") return t("accountPool.dashboard.healthy");
    if (environment.status === "error") return t("accountPool.dashboard.unhealthy");
    return t("accountPool.dashboard.checking");
  })();

  return (
    <Card
      data-testid={`account-pool-card-${environment.id}`}
      onDoubleClick={() => onManagePolicy(environment)}
      title={t("accountPool.dashboard.doubleClickToConfigure")}
    >
      <CardHeader className="gap-3">
        <div className="flex min-w-0 items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className="truncate text-base">{environment.name}</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              {t(`accountPool.channel.${environment.channel}`)} · {t(`accountPool.supplier.${environment.supplier}`)}
            </p>
            {environment.configuration_pending && (
              <p className="mt-1 text-xs text-muted-foreground" role="status">
                {t("accountPool.configurationSyncing")}
              </p>
            )}
            <p className="mt-1 text-xs text-muted-foreground">
              {t("accountPool.config.versions", {
                desired: environment.desired_configuration_version ?? 0,
                observed: environment.observed_configuration_version ?? 0,
              })}
            </p>
            {environment.status === "awaiting_authorization" && (
              <p className="mt-1 text-xs text-muted-foreground" role="status">
                {t("accountPool.preAuthorizationProxyHint")}
              </p>
            )}
          </div>
          <Badge variant={statusVariant(environment.status)}>{statusLabel(t, environment.status)}</Badge>
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
          <div className="flex items-center gap-1">
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
              onClick={() => onManagePolicy(environment)}
              disabled={disabled}
              aria-label={t("accountPool.policy.manage", { name: environment.name })}
              title={t("accountPool.policy.manageShort")}
            >
              <Settings2 />
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
            {canAuthorizeEnvironment(environment) && (
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
      <CardContent className="grid gap-4 text-sm">
        <div className="flex flex-wrap gap-1.5">
          {environment.quota.plan_type && <Badge variant="outline">{environment.quota.plan_type}</Badge>}
          {group && <Badge variant="secondary">{group}</Badge>}
          {tags.map((tag) => (
            <Badge key={tag} variant="outline">
              {tag}
            </Badge>
          ))}
        </div>
        <div className="min-w-0">
          <p className="text-xs text-muted-foreground">{t("accountPool.config.outboundProxy")}</p>
          <p className="mt-1 break-words font-medium">
            {environment.proxy_mode === "default_gateway" ? t("accountPool.config.defaultGateway") : proxyLabel}
          </p>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.requests")}</p>
            <p className="mt-1 font-medium">{requestStats?.total_requests ?? "-"}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.cardSuccessRate")}</p>
            <p className="mt-1 font-medium">{successRate === null ? "-" : `${successRate.toFixed(1)}%`}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.health")}</p>
            <p className="mt-1 font-medium">{healthLabel}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">{t("accountPool.remainingQuota")}</p>
            <p className="mt-1 font-medium">{formatQuota(t, quotaWindow)}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">{t("accountPool.nextReset")}</p>
            <p className="mt-1 font-medium">{formatDateTime(quotaWindow?.resets_at, i18n.language)}</p>
          </div>
        </div>
        {policyValues && (
          <div className="grid grid-cols-2 gap-3 border-t border-border pt-3">
            <div>
              <p className="text-xs text-muted-foreground">{t("accountPool.policy.strategy")}</p>
              <p className="mt-1 font-medium">
                {t(`accountPool.policy.options.${policyValues.routing?.strategy ?? "auto"}`)}
              </p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">{t("accountPool.policy.weight")}</p>
              <p className="mt-1 font-medium">{policyValues.routing?.weight ?? 1}</p>
            </div>
            {environment.supplier === "openai_codex" && policyValues.codex && (
              <div className="col-span-2">
                <p className="text-xs text-muted-foreground">{t("accountPool.policy.identity_fingerprint_mode")}</p>
                <p className="mt-1 font-medium">
                  {t(`accountPool.policy.options.${policyValues.codex.identity_fingerprint_mode}`)}
                </p>
              </div>
            )}
          </div>
        )}
        {providerSummary && (
          <div className="grid gap-1 border-t border-border pt-3">
            <p className="text-xs text-muted-foreground">{t("accountPool.policy.providerSummary")}</p>
            <p className="font-medium">{providerSummary}</p>
          </div>
        )}
        <div>
          <p className="text-xs text-muted-foreground">{t("accountPool.availableModels")}</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {environment.enabled_models.length > 0 ? (
              <>
                {visibleModels.map((model) => (
                  <Badge key={model} variant="outline">
                    {model}
                  </Badge>
                ))}
                {hiddenModelCount > 0 && (
                  <Badge
                    variant="secondary"
                    title={environment.enabled_models.slice(3).join("\n")}
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
        {environment.last_error && <p className="text-xs text-destructive">{environment.last_error}</p>}
        {onViewLogs && (
          <Button variant="outline" size="sm" onClick={() => onViewLogs(environment)}>
            {t("accountPool.logs.cardLogs")}
          </Button>
        )}
      </CardContent>
    </Card>
  );
};
