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
import type { AccountPoolEnvironment } from "./AccountPoolTypes";

interface AccountPoolCardProps {
  environment: AccountPoolEnvironment;
  onConfigure: (environment: AccountPoolEnvironment) => void;
  onEnabledChange: (environment: AccountPoolEnvironment, enabled: boolean) => void;
  onAuthorize: (environment: AccountPoolEnvironment) => void;
  onDelete: (environment: AccountPoolEnvironment) => void;
  disabled?: boolean;
}

export const AccountPoolCard = ({
  environment,
  onConfigure,
  onEnabledChange,
  onAuthorize,
  onDelete,
  disabled = false,
}: AccountPoolCardProps) => {
  const { t, i18n } = useTranslation();
  const quotaWindow = mostConstrainedWindow(environment);
  const authorizationAction =
    environment.status === "error" ? t("accountPool.reauthorize") : t("accountPool.continueAuthorization");

  return (
    <Card data-testid={`account-pool-card-${environment.id}`}>
      <CardHeader className="gap-3">
        <div className="flex min-w-0 items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className="truncate text-base">{environment.name}</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">{t("accountPool.provider")}</p>
            {environment.configuration_pending && (
              <p className="mt-1 text-xs text-muted-foreground" role="status">
                {t("accountPool.configurationSyncing")}
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
        <div className="grid grid-cols-2 gap-3">
          <div>
            <p className="text-xs text-muted-foreground">{t("accountPool.remainingQuota")}</p>
            <p className="mt-1 font-medium">{formatQuota(t, quotaWindow)}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">{t("accountPool.nextReset")}</p>
            <p className="mt-1 font-medium">{formatDateTime(quotaWindow?.resets_at, i18n.language)}</p>
          </div>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">{t("accountPool.availableModels")}</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {environment.enabled_models.length > 0 ? (
              environment.enabled_models.map((model) => (
                <Badge key={model} variant="outline">
                  {model}
                </Badge>
              ))
            ) : (
              <span className="text-muted-foreground">{t("accountPool.noEnabledModels")}</span>
            )}
          </div>
        </div>
        {environment.last_error && <p className="text-xs text-destructive">{environment.last_error}</p>}
      </CardContent>
    </Card>
  );
};
