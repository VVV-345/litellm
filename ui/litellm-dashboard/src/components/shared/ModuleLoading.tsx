"use client";

import { useTranslation } from "react-i18next";
import { Skeleton } from "@/components/ui/skeleton";

export default function ModuleLoading() {
  const { t } = useTranslation();
  return (
    <div role="status" aria-busy="true" aria-label={t("ui.Loading")} className="space-y-4 p-6">
      <Skeleton className="h-8 w-48" />
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}
