"use client";

import { MemoryView } from "./_components/MemoryView";
import { DeprecationBanner } from "@/components/DeprecationBanner";
import { AdminOnlyNotice } from "@/components/shared/AdminOnlyNotice";
import { useTranslation } from "react-i18next";
import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import useCan from "@/app/(dashboard)/hooks/useCan";

export default function Memory() {
  const { t } = useTranslation();
  const { accessToken, userRole, userId } = useAuthorized();
  const canViewMemory = useCan("viewMemory");

  if (!canViewMemory) {
    return <AdminOnlyNotice pageTitle={t("ui.Memory")} />;
  }

  return (
    <>
      <DeprecationBanner featureName={t("ui.Memory")} />
      <MemoryView accessToken={accessToken} userID={userId} userRole={userRole} />
    </>
  );
}
