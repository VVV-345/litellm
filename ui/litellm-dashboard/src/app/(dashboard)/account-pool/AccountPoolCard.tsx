/** 本文件渲染单个号池环境卡片，负责展示状态与触发页面级操作。 */

import { KeyRound, Trash2, Settings2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";

import {
  formatDateTime,
  formatQuota,
  canAuthorizeEnvironment,
  canConfigureEnvironment,
  canDeleteEnvironment,
  canToggleEnvironment,
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
  const quotaWindow = mostConstrainedWindow(environment);

  return (
    <Card data-testid={`account-pool-card-${environment.id}`}>
      <CardHeader className="gap-3">
        <div className="flex min-w-0 items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className="truncate text-base">{environment.name}</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">OpenAI Codex</p>
            {environment.configuration_pending && (
              <p className="mt-1 text-xs text-muted-foreground" role="status">
                配置同步中
              </p>
            )}
          </div>
          <Badge variant={statusVariant(environment.status)}>{statusLabel(environment.status)}</Badge>
        </div>
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <span>启用</span>
            <Switch
              checked={environment.enabled}
              onCheckedChange={(checked) => onEnabledChange(environment, checked === true)}
              disabled={disabled || !canToggleEnvironment(environment)}
              aria-label={`启用 ${environment.name}`}
            />
          </div>
          <div className="flex items-center gap-1">
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => onConfigure(environment)}
              disabled={disabled || !canConfigureEnvironment(environment)}
              aria-label={`配置 ${environment.name}`}
              title="配置环境"
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
                aria-label={`${environment.status === "error" ? "重新授权" : "继续授权"} ${environment.name}`}
                title={environment.status === "error" ? "重新授权" : "继续授权"}
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
              aria-label={`删除 ${environment.name}`}
              title="删除环境"
            >
              <Trash2 />
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="grid gap-4 text-sm">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <p className="text-xs text-muted-foreground">剩余额度</p>
            <p className="mt-1 font-medium">{formatQuota(quotaWindow)}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">下次重置</p>
            <p className="mt-1 font-medium">{formatDateTime(quotaWindow?.resets_at)}</p>
          </div>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">支持模型</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {environment.enabled_models.length > 0 ? (
              environment.enabled_models.map((model) => (
                <Badge key={model} variant="outline">
                  {model}
                </Badge>
              ))
            ) : (
              <span className="text-muted-foreground">未启用模型</span>
            )}
          </div>
        </div>
        {environment.last_error && <p className="text-xs text-destructive">{environment.last_error}</p>}
      </CardContent>
    </Card>
  );
};
