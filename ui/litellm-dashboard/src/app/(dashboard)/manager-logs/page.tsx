/** 管理员专用的 Manager 浏览器自动化日志路由。 */
"use client";

import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import ManagerLogsPanel from "@/features/account-pool/components/logs/ManagerLogsPanel";
import { isProxyAdminRole } from "@/utils/roles";

export default function ManagerLogsPage() {
  const { accessToken, userRole } = useAuthorized();
  if (!accessToken || !userRole) return null;
  if (!isProxyAdminRole(userRole)) {
    return <p role="alert" className="p-6">仅代理管理员可以查看 Manager 日志。</p>;
  }
  return <ManagerLogsPanel key={accessToken} accessToken={accessToken} />;
}
