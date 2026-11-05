/** 本文件展示 OAuth 和设备授权状态，并把授权操作绑定到具体号池卡片。 */

import { Bot, KeyRound, Network, Plus } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import { canAuthorizeEnvironment } from "../../utils/AccountPoolPermissions";
import { statusLabel, statusVariant } from "../../utils/AccountPoolFormatters";
import type { AccountPoolEnvironment } from "../../utils/AccountPoolTypes";
import type { AccountPoolSupplier } from "../../utils/AccountPoolTypes";
import { AccountPoolSupplierLogo } from "../providers/AccountPoolSupplierLogo";
import { useAccountPoolProxyOptions } from "../../hooks/useAccountPoolProxyOptions";

const OAUTH_PROVIDERS: readonly AccountPoolSupplier[] = [
  "kimi",
  "openai_codex",
  "anthropic_claude",
  "google_antigravity",
  "xai",
];

export const AccountPoolAuthorizationOverview = ({
  accessToken,
  active = true,
  environments,
  onCreate,
  onAuthorize,
  onAutomation,
}: {
  accessToken: string | null;
  active?: boolean;
  environments: readonly AccountPoolEnvironment[];
  onCreate: (supplier: AccountPoolSupplier) => void;
  onAuthorize: (environment: AccountPoolEnvironment) => void;
  onAutomation?: (environmentId?: string) => void;
}) => {
  const { t } = useTranslation();
  const { defaultLabel } = useAccountPoolProxyOptions(accessToken, active);
  const cards = environments.filter((environment) => environment.authorization_flow !== "direct_credential");
  return (
    <div className="grid gap-4">
      {onAutomation && (
        <section
          aria-label="浏览器自动化授权"
          className="rounded-xl border border-blue-200 bg-blue-50/40 p-4 dark:border-blue-900 dark:bg-blue-950/20"
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex min-w-0 items-start gap-3">
              <Bot className="mt-0.5 size-5 shrink-0 text-blue-600 dark:text-blue-400" aria-hidden="true" />
              <div>
                <h3 className="text-sm font-semibold">Codex 浏览器自动化</h3>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  空白卡片与出口代理、固定指纹、自动登录与 2FA、回调与模型验收，复用同一授权队列
                </p>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  不覆盖已有凭据；遇到验证码或额外验证需人工接管。其他供应商仍使用下方原有授权方式
                </p>
              </div>
            </div>
            <Button type="button" size="sm" variant="outline" onClick={() => onAutomation()}>
              浏览器自动化授权
            </Button>
          </div>
        </section>
      )}
      <p
        className="flex items-center gap-2 rounded-lg border bg-muted/20 px-3 py-2 text-xs text-muted-foreground"
        role="status"
      >
        <Network className="size-3.5 shrink-0" aria-hidden="true" />
        {defaultLabel}
      </p>
      <div className="flex flex-wrap gap-2" aria-label="新建 OAuth 授权">
        {OAUTH_PROVIDERS.map((supplier) => (
          <Button key={supplier} type="button" size="sm" variant="outline" onClick={() => onCreate(supplier)}>
            <AccountPoolSupplierLogo supplier={supplier} className="size-4" />
            {t(`accountPool.supplier.${supplier}`)}
            <Plus className="size-3.5" />
          </Button>
        ))}
      </div>
      <div className="grid gap-3 xl:grid-cols-2">
        {cards.length === 0 && <p className="p-4 text-sm text-muted-foreground">暂无 OAuth 卡片，可从上方新建授权。</p>}
        {cards.map((environment) => {
          const codexOAuth =
            environment.supplier === "openai_codex" && environment.authorization_flow === "browser_oauth";
          const awaitingAuthorization = environment.status === "awaiting_authorization" && environment.enabled;
          const proxyReady = Boolean(environment.proxy_profile_id) && !environment.configuration_pending;
          const codexReady = codexOAuth && awaitingAuthorization;
          const canAutomate = Boolean(onAutomation) && codexReady && proxyReady;
          return (
            <div
              key={environment.id}
              className="flex min-w-0 flex-wrap items-center justify-between gap-3 rounded-xl border bg-card p-3 shadow-sm"
            >
              <div className="flex min-w-0 flex-1 items-center gap-3">
                <div className="flex size-10 shrink-0 items-center justify-center rounded-lg border bg-muted/30">
                  <AccountPoolSupplierLogo supplier={environment.supplier} className="size-5" />
                </div>
                <div className="min-w-0">
                  <span className="block truncate text-sm font-medium">{environment.name}</span>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {environment.configuration_pending
                      ? t("accountPool.oauth.configurationPending")
                      : t("accountPool.oauth.credentialStored")}
                  </p>
                </div>
              </div>
              <Badge
                variant={statusVariant(environment.status)}
                className={
                  environment.status === "ready"
                    ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                    : undefined
                }
              >
                {statusLabel(t, environment.status)}
              </Badge>
              <div className="flex flex-wrap gap-2">
                {canAutomate && onAutomation && (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => onAutomation(environment.id)}
                    aria-label={`自动化授权 ${environment.name}`}
                  >
                    <Bot aria-hidden="true" />
                    自动化授权
                  </Button>
                )}
                {canAuthorizeEnvironment(environment) && (
                  <Button type="button" size="sm" variant="outline" onClick={() => onAuthorize(environment)}>
                    <KeyRound />
                    {t("accountPool.oauth.reauthorize")}
                  </Button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
