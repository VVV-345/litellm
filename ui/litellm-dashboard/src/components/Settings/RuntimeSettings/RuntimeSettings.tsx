"use client";

import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { canManageAccountPool } from "@/features/account-pool/utils/AccountPoolPermissions";
import { useAccountPoolQuery } from "@/features/account-pool/hooks/useAccountPoolQuery";
import { RuntimeSettingsSection, type RuntimeSettingsCategory } from "./RuntimeSettingsSection";

export default function RuntimeSettings({ category }: { category: RuntimeSettingsCategory }) {
  const { accessToken, userRole } = useAuthorized();
  const allowed = canManageAccountPool(userRole, false);
  const accounts = useAccountPoolQuery(accessToken, allowed, false);
  if (!allowed || !accessToken) return null;
  if (accounts.isPending) return <p>正在读取卡片配置…</p>;
  if (accounts.isError) return <p role="alert">卡片配置读取失败，请刷新重试</p>;
  if (!accounts.data?.length) return null;
  return <RuntimeSettingsSection accessToken={accessToken} environments={accounts.data} category={category} />;
}
