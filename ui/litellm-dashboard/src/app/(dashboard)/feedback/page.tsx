"use client";

import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { AdminOnlyNotice } from "@/components/shared/AdminOnlyNotice";
import { AccountPoolFeedbackPanel } from "@/features/account-pool/components/feedback/AccountPoolFeedbackPanel";
import { canManageAccountPool } from "@/features/account-pool/utils/AccountPoolPermissions";

export default function FeedbackCenterPage() {
  const { accessToken, userRole, isViewOnly } = useAuthorized();
  const canManage = canManageAccountPool(userRole, isViewOnly);
  if (!canManage) return <AdminOnlyNotice pageTitle="反馈中心" />;
  if (!accessToken) return null;
  return (
    <div className="grid gap-4 p-4 lg:p-6">
      <AccountPoolFeedbackPanel accessToken={accessToken} />
    </div>
  );
}
