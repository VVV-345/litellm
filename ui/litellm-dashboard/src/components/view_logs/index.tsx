import dynamic from "next/dynamic";
import ModuleLoading from "@/components/shared/ModuleLoading";
import { schedulePreloads } from "@/lib/progressivePreload";

const loadOperationLogs = () => import("./OperationLogsPanel").then((module) => module.OperationLogsPanel);
const loadFullLogs = () => import("./FullLogsPanel").then((module) => module.FullLogsPanel);
const loadLogSettings = () => import("./LogSettingsPanel").then((module) => module.LogSettingsPanel);
const LOG_MODULE_LOADERS = [loadOperationLogs, loadFullLogs, loadLogSettings];

const OperationLogsPanel = dynamic(loadOperationLogs, {
  loading: ModuleLoading,
});
const FullLogsPanel = dynamic(loadFullLogs, {
  loading: ModuleLoading,
});
const LogSettingsPanel = dynamic(loadLogSettings, {
  loading: ModuleLoading,
});
const DeletedKeysPage = dynamic(() => import("../DeletedKeysPage/DeletedKeysPage"), { loading: ModuleLoading });
const DeletedTeamsPage = dynamic(() => import("../DeletedTeamsPage/DeletedTeamsPage"), { loading: ModuleLoading });
const AuditLogsPanel = dynamic(() => import("./AuditLogsPanel"), { loading: ModuleLoading });

import { parseAsString, useQueryStates } from "nuqs";
import { useEffect, useState } from "react";
import { DeferredTabPanel } from "@/components/shared/DeferredTabPanel";
import { useAccountPoolQuery } from "@/features/account-pool/hooks/useAccountPoolQuery";
import { canManageAccountPool } from "@/features/account-pool/utils/AccountPoolPermissions";
import { useTranslation } from "react-i18next";
import useCan from "@/app/(dashboard)/hooks/useCan";
import RequestLogsPanel from "./RequestLogsPanel";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { UiLoadingSpinner } from "@/components/ui/ui-loading-spinner";

interface SpendLogsTableProps {
  accessToken: string | null;
  token: string | null;
  userRole: string | null;
  userID: string | null;
  premiumUser: boolean;
}

type LogsTabId = "operations" | "full" | "settings" | "request logs" | "audit logs" | "deleted keys" | "deleted teams";

interface LogsTab {
  id: LogsTabId;
  label: string;
}

const REQUEST_LOGS_TAB: LogsTab = { id: "request logs", label: "Request Logs" };
const AUDIT_LOGS_TAB: LogsTab = { id: "audit logs", label: "Audit Logs" };
const DELETED_KEYS_TAB: LogsTab = { id: "deleted keys", label: "Deleted Keys" };
const DELETED_TEAMS_TAB: LogsTab = { id: "deleted teams", label: "Deleted Teams" };

export default function SpendLogsTable({ accessToken, token, userRole, userID, premiumUser }: SpendLogsTableProps) {
  const { t } = useTranslation();
  const [{ log_view, account_id }, setLogParams] = useQueryStates({
    log_view: parseAsString.withDefault("request logs"),
    account_id: parseAsString,
  });
  const canManageLogs = canManageAccountPool(userRole, false);
  useEffect(() => {
    if (!canManageLogs || !accessToken) return;
    return schedulePreloads(LOG_MODULE_LOADERS);
  }, [accessToken, canManageLogs]);
  const canViewAuditLogs = useCan("viewAuditLogs");
  const canViewDeletedTeams = useCan("viewDeletedTeams");
  const [visitedTabs, setVisitedTabs] = useState<string[]>([]);
  const accounts = useAccountPoolQuery(
    accessToken,
    canManageLogs && !["settings", "audit logs", "deleted keys", "deleted teams"].includes(log_view),
    false,
  );

  if (!accessToken || !token || !userRole || !userID) {
    return (
      <div
        role="status"
        aria-busy="true"
        aria-label={t("ui.Loading")}
        className="flex h-64 items-center justify-center"
      >
        <UiLoadingSpinner className="size-8 text-primary" />
      </div>
    );
  }

  const tabs: LogsTab[] = [
    { ...REQUEST_LOGS_TAB, label: t("ui.Request Logs") },
    ...(canManageLogs
      ? [
          { id: "operations" as const, label: "运行日志" },
          { id: "full" as const, label: "完整日志" },
          { id: "settings" as const, label: "日志设置" },
        ]
      : []),
    ...(canViewAuditLogs ? [{ ...AUDIT_LOGS_TAB, label: t("ui.Audit Logs") }] : []),
    { ...DELETED_KEYS_TAB, label: t("ui.Deleted Keys") },
    ...(canViewDeletedTeams ? [{ ...DELETED_TEAMS_TAB, label: t("ui.Deleted Teams") }] : []),
  ];
  const activeTab = tabs.find((tab) => tab.id === log_view)?.id ?? "request logs";

  const renderPanel = (tabId: LogsTabId) => {
    switch (tabId) {
      case "request logs":
        return (
          <RequestLogsPanel
            key={account_id ?? "all"}
            accessToken={accessToken}
            token={token}
            userRole={userRole}
            userID={userID}
            isActive={activeTab === "request logs"}
            accountId={account_id ?? undefined}
          />
        );
      case "operations":
        return (
          <OperationLogsPanel
            key={account_id ?? "all"}
            accessToken={accessToken}
            environments={accounts.data ?? []}
            initialCardId={account_id ?? undefined}
          />
        );
      case "full":
        return (
          <FullLogsPanel
            key={account_id ?? "all"}
            accessToken={accessToken}
            environments={accounts.data ?? []}
            initialCardId={account_id ?? undefined}
          />
        );
      case "settings":
        return activeTab === "settings" ? <LogSettingsPanel accessToken={accessToken} /> : null;
      case "audit logs":
        return (
          <AuditLogsPanel
            userID={userID}
            userRole={userRole}
            token={token}
            accessToken={accessToken}
            isActive={activeTab === "audit logs"}
            premiumUser={premiumUser}
          />
        );
      case "deleted keys":
        return activeTab === "deleted keys" ? <DeletedKeysPage /> : null;
      case "deleted teams":
        return activeTab === "deleted teams" ? <DeletedTeamsPage /> : null;
    }
  };

  return (
    <div className="box-border w-full overflow-x-hidden p-6">
      {canManageLogs && ["request logs", "operations", "full"].includes(activeTab) && (
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <label htmlFor="logs-account">账号筛选</label>
          <select
            id="logs-account"
            className="rounded-md border bg-background px-3 py-2 text-sm"
            value={account_id ?? ""}
            onChange={(event) => void setLogParams({ account_id: event.target.value || null })}
          >
            <option value="">全部账号与供应商</option>
            {account_id && !accounts.data?.some((account) => account.id === account_id) && (
              <option value={account_id}>{account_id}</option>
            )}
            {accounts.data?.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
              </option>
            ))}
          </select>
          {accounts.isError && (
            <p role="alert" className="text-sm">
              账号列表暂不可用，已有日志仍可查询
            </p>
          )}
        </div>
      )}
      <Tabs
        value={activeTab}
        onValueChange={(value) => {
          setVisitedTabs((visited) => Array.from(new Set([...visited, activeTab, value])));
          void setLogParams({ log_view: value });
        }}
      >
        <TabsList variant="line">
          {tabs.map((tab) => (
            <TabsTrigger key={tab.id} value={tab.id} className="flex-none">
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>
        {tabs.map((tab) => (
          <DeferredTabPanel
            key={tab.id}
            value={tab.id}
            active={tab.id === activeTab}
            visited={visitedTabs.includes(tab.id)}
          >
            {renderPanel(tab.id)}
          </DeferredTabPanel>
        ))}
      </Tabs>
    </div>
  );
}
