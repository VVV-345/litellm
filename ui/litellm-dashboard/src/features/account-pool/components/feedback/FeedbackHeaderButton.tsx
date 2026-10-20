"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import { useTranslation } from "react-i18next";
import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { Button } from "@/components/ui/button";
import { canManageAccountPool } from "../../utils/AccountPoolPermissions";

const FeedbackIssueDialog = dynamic(() => import("./FeedbackIssueDialog").then((module) => module.FeedbackIssueDialog));

export function FeedbackHeaderButton() {
  const { accessToken, userRole, isViewOnly } = useAuthorized();
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (!accessToken || !canManageAccountPool(userRole, isViewOnly)) return null;
  return <>
    <Button size="sm" variant="outline" onClick={() => setOpen(true)}>{t("feedback.create")}</Button>
    {open && <FeedbackIssueDialog key={accessToken} accessToken={accessToken} pagePath={window.location.pathname}
      onClose={() => setOpen(false)} onSaved={() => setOpen(false)} />}
  </>;
}
