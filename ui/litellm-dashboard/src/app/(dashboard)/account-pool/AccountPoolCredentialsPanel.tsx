/** 本文件展示凭据文件的脱敏状态和所属卡片，不返回任何令牌或完整配置。 */

import { FileKey2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { statusLabel, statusVariant } from "./AccountPoolFormatters";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";

export const AccountPoolCredentialsPanel = ({ environments }: { environments: readonly AccountPoolEnvironment[] }) => {
  const { t } = useTranslation();
  return (
    <div className="grid gap-5">
      <div>
        <h2 className="text-lg font-semibold">{t("accountPool.credentials.title")}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.credentials.description")}</p>
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {environments.map((environment) => (
          <Card key={environment.id}>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2">
                  <FileKey2 className="size-5 shrink-0 text-primary" />
                  <CardTitle className="truncate text-base">{environment.name}</CardTitle>
                </div>
                <Badge variant={statusVariant(environment.status)}>{statusLabel(t, environment.status)}</Badge>
              </div>
            </CardHeader>
            <CardContent className="grid gap-2 text-sm">
              <div className="flex items-center justify-between gap-3">
                <span className="text-muted-foreground">{t("accountPool.credentials.provider")}</span>
                <span>{t(`accountPool.supplier.${environment.supplier}`)}</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-muted-foreground">{t("accountPool.credentials.models")}</span>
                <span>{environment.available_models.length}</span>
              </div>
              <p className="text-xs text-muted-foreground">{t("accountPool.credentials.secretHint")}</p>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
};
