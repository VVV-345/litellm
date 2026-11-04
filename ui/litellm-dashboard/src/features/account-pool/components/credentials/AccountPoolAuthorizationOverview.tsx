/** 本文件展示 OAuth 和设备授权状态，并把授权操作绑定到具体号池卡片。 */

import { KeyRound, Plus } from "lucide-react";
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
      <p className="text-xs text-muted-foreground" role="status">
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
      <div className="divide-y rounded-lg border">
        {cards.length === 0 && <p className="p-4 text-sm text-muted-foreground">暂无 OAuth 卡片，可从上方新建授权。</p>}
        {cards.map((environment) => (
          <div key={environment.id} className="flex flex-wrap items-center justify-between gap-3 px-3 py-2.5">
            <div className="flex min-w-0 flex-1 items-center gap-2">
              <AccountPoolSupplierLogo supplier={environment.supplier} className="size-4 shrink-0" />
              <span className="truncate text-sm font-medium">{environment.name}</span>
              <Badge variant={statusVariant(environment.status)}>{statusLabel(t, environment.status)}</Badge>
            </div>
            <span className="text-xs text-muted-foreground">
              {environment.configuration_pending
                ? t("accountPool.oauth.configurationPending")
                : t("accountPool.oauth.credentialStored")}
            </span>
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
