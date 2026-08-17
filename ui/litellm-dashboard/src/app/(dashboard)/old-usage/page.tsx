"use client";

import Usage from "./_components/usage";
import { DeprecationBanner } from "@/components/DeprecationBanner";
import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { useTranslation } from "react-i18next";

export default function OldUsagePage() {
  const { t } = useTranslation();
  const { accessToken, token, userRole, userId: userID, premiumUser } = useAuthorized();
  return (
    <>
      <DeprecationBanner featureName={t("ui.The old Usage page")} />
      <Usage
        accessToken={accessToken}
        token={token}
        userRole={userRole}
        userID={userID}
        keys={null}
        premiumUser={premiumUser}
      />
    </>
  );
}
