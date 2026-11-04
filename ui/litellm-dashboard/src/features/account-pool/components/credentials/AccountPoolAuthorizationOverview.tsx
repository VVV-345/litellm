/** 本文件展示 OAuth 和设备授权状态，并把授权操作绑定到具体号池卡片。 */

import { KeyRound, Network, Plus } from "lucide-react";
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
}: {
  accessToken: string | null;
  active?: boolean;
  environments: readonly AccountPoolEnvironment[];
  onCreate: (supplier: AccountPoolSupplier) => void;
  onAuthorize: (environment: AccountPoolEnvironment) => void;
}) => {
  const { t } = useTranslation();
  const { defaultLabel } = useAccountPoolProxyOptions(accessToken, active);
  const cards = environments.filter((environment) => environment.authorization_flow !== "direct_credential");
  return (
    <div className="grid gap-4">
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
        {cards.map((environment) => (
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
            {canAuthorizeEnvironment(environment) && (
              <Button type="button" size="sm" variant="outline" onClick={() => onAuthorize(environment)}>
                <KeyRound />
                {t("accountPool.oauth.reauthorize")}
              </Button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
};
