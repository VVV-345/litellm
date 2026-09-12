/** 本文件展示 OAuth 和设备授权状态，并把授权操作绑定到具体号池卡片。 */

import { KeyRound, Plus } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { canAuthorizeEnvironment } from "./AccountPoolPermissions";
import { statusLabel, statusVariant } from "./AccountPoolFormatters";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";
import type { AccountPoolSupplier } from "./AccountPoolTypes";

const OAUTH_PROVIDERS: readonly AccountPoolSupplier[] = [
  "kimi",
  "openai_codex",
  "anthropic_claude",
  "google_antigravity",
  "xai",
];

export const AccountPoolAuthorizationOverview = ({
  environments,
  onCreate,
  onAuthorize,
}: {
  environments: readonly AccountPoolEnvironment[];
  onCreate: (supplier: AccountPoolSupplier) => void;
  onAuthorize: (environment: AccountPoolEnvironment) => void;
}) => {
  const { t } = useTranslation();
  return (
    <div className="grid gap-5">
      <div>
        <h2 className="text-lg font-semibold">{t("accountPool.oauth.title")}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.oauth.description")}</p>
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
        {OAUTH_PROVIDERS.map((supplier) => {
          const cards = environments.filter((environment) => environment.supplier === supplier);
          return (
            <Card key={supplier} className="flex flex-col">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">{t(`accountPool.supplier.${supplier}`)}</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-1 flex-col justify-between gap-3 text-sm">
                <div className="flex items-center justify-between border-b pb-2">
                  <span className="text-muted-foreground">{t("accountPool.oauth.cardCount")}</span>
                  <Badge variant="outline">{cards.length}</Badge>
                </div>
                <Button type="button" size="sm" onClick={() => onCreate(supplier)}>
                  <Plus />
                  {t("accountPool.oauth.start")}
                </Button>
              </CardContent>
            </Card>
          );
        })}
      </div>
      <div className="border-t pt-5">
        <h3 className="mb-3 text-base font-semibold">{t("accountPool.oauth.cardsTitle")}</h3>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {environments
            .filter((environment) => environment.authorization_flow !== "direct_credential")
            .map((environment) => (
              <Card key={environment.id}>
                <CardHeader className="pb-3">
                  <div className="flex items-center justify-between gap-3">
                    <CardTitle className="truncate text-base">{environment.name}</CardTitle>
                    <Badge variant={statusVariant(environment.status)}>{statusLabel(t, environment.status)}</Badge>
                  </div>
                </CardHeader>
                <CardContent className="flex items-center justify-between gap-3 text-sm">
                  <div>
                    <p className="text-muted-foreground">{t(`accountPool.supplier.${environment.supplier}`)}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {environment.configuration_pending
                        ? t("accountPool.oauth.configurationPending")
                        : t("accountPool.oauth.credentialStored")}
                    </p>
                  </div>
                  {canAuthorizeEnvironment(environment) && (
                    <Button type="button" size="sm" variant="outline" onClick={() => onAuthorize(environment)}>
                      <KeyRound />
                      {t("accountPool.oauth.reauthorize")}
                    </Button>
                  )}
                </CardContent>
              </Card>
            ))}
        </div>
      </div>
    </div>
  );
};
