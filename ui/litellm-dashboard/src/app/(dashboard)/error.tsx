"use client";

import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { updateDashboard } from "@/components/DashboardRecovery";
import { isModuleLoadError, reportModuleLoadError } from "@/lib/dashboardRecovery";

export default function DashboardError({ error, reset }: { error: Error; reset: () => void }) {
  const { t } = useTranslation();
  useEffect(() => {
    reportModuleLoadError(error);
  }, [error]);
  return (
    <div role="alert" className="space-y-3 p-6">
      <p>{t("dashboardRecovery.pageFailed")}</p>
      <p className="text-sm text-muted-foreground">{t("dashboardRecovery.keepDraft")}</p>
      <div className="flex gap-2">
        <Button variant="outline" onClick={isModuleLoadError(error) ? updateDashboard : reset}>
          {t("dashboardRecovery.retry")}
        </Button>
        <Button onClick={updateDashboard}>{t("dashboardRecovery.update")}</Button>
      </div>
    </div>
  );
}
