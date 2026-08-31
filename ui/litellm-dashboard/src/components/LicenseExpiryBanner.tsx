"use client";

import React, { useState } from "react";
import { CircleAlert, TriangleAlert, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/shared/Alert";
import { Button } from "@/components/ui/button";
import { LicenseInfo } from "@/components/networking";
import { useLicenseInfo } from "@/app/(dashboard)/hooks/license/useLicenseInfo";
import { formatExpiryDate, getDaysUntilExpiration, getLicenseExpiryTier } from "@/utils/licenseUtils";

const DISMISS_KEY_PREFIX = "litellm:licenseExpiryBannerDismissed:";
const SALES_EMAIL = "sales@berri.ai";

interface LicenseExpiryBannerProps {
  accessToken: string | null;
}

interface LicenseExpiryBannerViewProps {
  licenseInfo: LicenseInfo | null;
}

const describeCountdownKey = (days: number): "common.licenseExpiresToday" | "common.licenseExpiresOneDay" | "common.licenseExpiresDays" => {
  if (days <= 0) {
    return "common.licenseExpiresToday";
  }
  if (days === 1) {
    return "common.licenseExpiresOneDay";
  }
  return "common.licenseExpiresDays";
};

const descriptionKeyFor = (tier: "warning" | "critical" | "expired") => {
  if (tier === "expired") return "common.licenseExpiredDescription";
  if (tier === "critical") return "common.licenseCriticalDescription";
  return "common.licenseWarningDescription";
};

export const LicenseExpiryBannerView: React.FC<LicenseExpiryBannerViewProps> = ({ licenseInfo }) => {
  const [locallyDismissed, setLocallyDismissed] = useState(false);
  const { t } = useTranslation();

  const expirationDate = licenseInfo?.expiration_date ?? null;
  const tier = getLicenseExpiryTier(expirationDate);
  const days = getDaysUntilExpiration(expirationDate);

  if (expirationDate === null || tier === "none" || days === null) {
    return null;
  }

  const isDismissible = tier === "warning";
  const dismissKey = `${DISMISS_KEY_PREFIX}${expirationDate}`;
  const previouslyDismissed =
    isDismissible && typeof window !== "undefined" ? sessionStorage.getItem(dismissKey) === "true" : false;

  if (isDismissible && (locallyDismissed || previouslyDismissed)) {
    return null;
  }

  const formattedDate = formatExpiryDate(expirationDate);

  const message = tier === "expired"
    ? t("common.licenseExpired", { date: formattedDate })
    : t("common.licenseExpires", {
        countdown: t(describeCountdownKey(days), { days }),
        date: formattedDate,
      });
  const descriptionKey = descriptionKeyFor(tier);
  const description = t(descriptionKey, { email: SALES_EMAIL });

  const handleClose = () => {
    if (typeof window !== "undefined") {
      sessionStorage.setItem(dismissKey, "true");
    }
    setLocallyDismissed(true);
  };

  return (
    <Alert variant={tier === "warning" ? "warning" : "error"} className="rounded-none border-x-0 border-t-0">
      {tier === "warning" ? (
        <TriangleAlert className="size-4" aria-hidden />
      ) : (
        <CircleAlert className="size-4" aria-hidden />
      )}
      <AlertTitle>{message}</AlertTitle>
      <AlertDescription>{description}</AlertDescription>
      {isDismissible && (
        <AlertAction>
          <Button variant="ghost" size="icon-sm" aria-label={t("common.close")} onClick={handleClose}>
            <X className="size-4" />
          </Button>
        </AlertAction>
      )}
    </Alert>
  );
};

export const LicenseExpiryBanner: React.FC<LicenseExpiryBannerProps> = ({ accessToken }) => {
  const { data } = useLicenseInfo(accessToken);
  return <LicenseExpiryBannerView licenseInfo={data ?? null} />;
};
