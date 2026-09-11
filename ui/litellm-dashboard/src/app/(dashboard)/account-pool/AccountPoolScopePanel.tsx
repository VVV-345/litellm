/** 本文件说明全局默认与卡片覆盖的作用域，并提供进入单卡片配置的入口。 */

import { Settings2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import type { AccountPoolEnvironment } from "./AccountPoolTypes";

export const AccountPoolScopePanel = ({
  environments,
  onConfigure,
}: {
  environments: readonly AccountPoolEnvironment[];
  onConfigure: (environment: AccountPoolEnvironment) => void;
}) => {
  const { t } = useTranslation();
  return (
    <div className="grid gap-5">
      <div>
        <h2 className="text-lg font-semibold">{t("accountPool.scope.title")}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.scope.description")}</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("accountPool.scope.inheritanceTitle")}</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-2 text-sm text-muted-foreground">
          <p>{t("accountPool.scope.inheritanceRule")}</p>
          <p>{t("accountPool.scope.cardRule")}</p>
          <p>{t("accountPool.scope.proxyRule")}</p>
        </CardContent>
      </Card>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {environments.map((environment) => (
          <Card key={environment.id}>
            <CardContent className="flex items-center justify-between gap-3 p-4">
              <div className="min-w-0">
                <p className="truncate font-medium">{environment.name}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {t("accountPool.scope.cardOverride", { version: environment.version })}
                </p>
              </div>
              <Button type="button" variant="outline" size="sm" onClick={() => onConfigure(environment)}>
                <Settings2 />
                {t("accountPool.configure")}
              </Button>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
};
