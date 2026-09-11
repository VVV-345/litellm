/** 本文件展示 OAuth 和设备授权状态，并把授权操作绑定到具体号池卡片。 */

import { KeyRound } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { canAuthorizeEnvironment } from "./AccountPoolPermissions";
import { statusLabel, statusVariant } from "./AccountPoolFormatters";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";

export const AccountPoolAuthorizationOverview = ({
  environments,
  onAuthorize,
}: {
  environments: readonly AccountPoolEnvironment[];
  onAuthorize: (environment: AccountPoolEnvironment) => void;
}) => {
  const { t } = useTranslation();
  return (
    <div className="grid gap-5">
      <div>
        <h2 className="text-lg font-semibold">{t("accountPool.oauth.title")}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.oauth.description")}</p>
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {environments.map((environment) => (
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
  );
};
