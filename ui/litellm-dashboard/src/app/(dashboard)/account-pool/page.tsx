/** 本文件提供号池管理首页，编排数据查询、卡片操作、创建授权和配置弹窗。 */

"use client";

import { RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";

import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { AdminOnlyNotice } from "@/components/shared/AdminOnlyNotice";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "@/lib/toast";

import { AccountPoolCard } from "./AccountPoolCard";
import { AccountPoolBatchPanel } from "./AccountPoolBatchPanel";
import { AccountPoolConfigDialog } from "./AccountPoolConfigDialog";
import { AccountPoolDashboard } from "./AccountPoolDashboard";
import { AccountPoolKeyDialog } from "./AccountPoolKeyDialog";
import { AccountPoolLogsPanel } from "./AccountPoolLogsPanel";
import { AccountPoolAuthorizationOverview } from "./AccountPoolAuthorizationOverview";
import { AccountPoolCredentialsPanel } from "./AccountPoolCredentialsPanel";
import { AccountPoolQuotaPanel } from "./AccountPoolQuotaPanel";
import { AccountPoolSettingsPanel } from "./AccountPoolSettingsPanel";
import { AccountPoolPluginsPanel } from "./AccountPoolPluginsPanel";
import { AccountPoolPolicyDialog } from "./AccountPoolPolicyDialog";
import { AccountPoolProviderFamilies } from "./AccountPoolProviderFamilies";
import {
  getAccountPoolDashboardStats,
  listAccountPolicies,
  refreshAccountPoolQuotas,
  type ErrorStats,
} from "./AccountPoolManagementApi";
import { AccountPoolCreateDialog } from "./AccountPoolCreateDialog";
import { canManageAccountPool } from "./AccountPoolPermissions";
import type {
  AccountPoolAuthorization,
  AccountPoolEnvironment,
  AccountPoolStatus,
  AccountPoolSupplier,
} from "./AccountPoolTypes";
import {
  filterAccountPoolEnvironments,
  paginateAccountPoolEnvironments,
  summarizeAccountPoolEnvironments,
} from "./accountPoolSelectors";
import { ProxyManagerPanel } from "./ProxyManagerPanel";
import { useAccountPoolMutations } from "./useAccountPoolMutations";
import { useAccountPoolQuery } from "./useAccountPoolQuery";
import { useProxyGatewayQuery } from "./useProxyGateways";

const PAGE_SIZE = 24;
const STATUS_FILTERS: ReadonlyArray<"all" | AccountPoolStatus> = [
  "all",
  "provisioning",
  "awaiting_authorization",
  "validating",
  "ready",
  "cooling_down",
  "disabled",
  "error",
  "deleting",
];

export default function AccountPoolPage() {
  const { t } = useTranslation();
  const { accessToken, userRole, isViewOnly } = useAuthorized();
  const [createOpen, setCreateOpen] = useState(false);
  const [authorization, setAuthorization] = useState<AccountPoolAuthorization | null>(null);
  const [configEnvironment, setConfigEnvironment] = useState<AccountPoolEnvironment | null>(null);
  const [deleteEnvironment, setDeleteEnvironment] = useState<AccountPoolEnvironment | null>(null);
  const [keyEnvironment, setKeyEnvironment] = useState<AccountPoolEnvironment | null>(null);
  const [policyEnvironment, setPolicyEnvironment] = useState<AccountPoolEnvironment | null>(null);
  const [activeTab, setActiveTab] = useState("dashboard");
  const [createSupplier, setCreateSupplier] = useState<AccountPoolSupplier>("openai_codex");
  const [logCardId, setLogCardId] = useState<string | undefined>();
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | AccountPoolStatus>("all");
  const [page, setPage] = useState(1);
  const canManage = canManageAccountPool(userRole, isViewOnly);
  const environmentsQuery = useAccountPoolQuery(accessToken, canManage);
  const gatewaysQuery = useProxyGatewayQuery(accessToken, canManage);
  const policiesQueryOptions = {
    queryKey: ["account-pool", "policies", accessToken],
    queryFn: () => listAccountPolicies(accessToken!),
    enabled: canManage && accessToken !== null,
    retry: false,
  };
  const policiesQuery = useQuery(policiesQueryOptions);
  const { updateMutation, authorizeMutation, deleteMutation } = useAccountPoolMutations(
    accessToken,
    canManage,
    (result) => {
      setAuthorization(result);
      setCreateOpen(true);
    },
    () => setDeleteEnvironment(null),
  );
  const environments = useMemo(() => environmentsQuery.data ?? [], [environmentsQuery.data]);
  const policies = useMemo(() => policiesQuery.data ?? [], [policiesQuery.data]);
  const dashboardStatsQuery = useQuery({
    queryKey: ["account-pool", "dashboard-stats", accessToken],
    queryFn: () => getAccountPoolDashboardStats(accessToken!),
    enabled: canManage && accessToken !== null,
    retry: false,
    staleTime: 30_000,
  });
  const statsByCard = useMemo(
    () =>
      new Map<string, ErrorStats>(
        (dashboardStatsQuery.data?.cards ?? []).flatMap((stats) =>
          stats.card_id ? [[stats.card_id, stats] as [string, ErrorStats]] : [],
        ),
      ),
    [dashboardStatsQuery.data?.cards],
  );
  const statsLoading = dashboardStatsQuery.isPending || dashboardStatsQuery.isFetching;
  const filteredEnvironments = useMemo(
    () => filterAccountPoolEnvironments(environments, search, statusFilter, policies),
    [environments, policies, search, statusFilter],
  );
  const pageCount = Math.max(1, Math.ceil(filteredEnvironments.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const visibleEnvironments = paginateAccountPoolEnvironments(filteredEnvironments, currentPage, PAGE_SIZE);
  const overview = useMemo(() => summarizeAccountPoolEnvironments(environments), [environments]);
  const busy = updateMutation.isPending || deleteMutation.isPending || authorizeMutation.isPending;
  const showAccountFilters = !environmentsQuery.isLoading && !environmentsQuery.isError && environments.length > 0;

  if (!canManage) return <AdminOnlyNotice pageTitle={t("accountPool.title")} />;

  const openCreateDialog = (supplier: AccountPoolSupplier = "openai_codex") => {
    setAuthorization(null);
    setCreateSupplier(supplier);
    setCreateOpen(true);
  };

  const renderCard = (environment: AccountPoolEnvironment, requestStats?: ErrorStats) => (
    <AccountPoolCard
      key={environment.id}
      environment={environment}
      requestStats={requestStats}
      proxyGateway={gatewaysQuery.data?.find((gateway) => gateway.profile_id === environment.proxy_profile_id)}
      onConfigure={setConfigEnvironment}
      onEnabledChange={(current, enabled) => updateMutation.mutate({ environment: current, enabled })}
      onAuthorize={(current) => authorizeMutation.mutate(current)}
      onDelete={setDeleteEnvironment}
      onManageKey={setKeyEnvironment}
      onManagePolicy={setPolicyEnvironment}
      onViewLogs={(current) => {
        setLogCardId(current.id);
        setActiveTab("logs");
      }}
      tags={policiesQuery.data?.find((item) => item.card_id === environment.id)?.policy?.tags}
      group={policiesQuery.data?.find((item) => item.card_id === environment.id)?.policy?.group}
      policy={policiesQuery.data?.find((item) => item.card_id === environment.id)}
      disabled={busy}
    />
  );

  const renderContent = () => {
    if (environmentsQuery.isLoading) {
      return (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {["one", "two", "three"].map((key) => (
            <Skeleton key={key} className="h-64 w-full" />
          ))}
        </div>
      );
    }
    if (environmentsQuery.isError) {
      const managerNotConfigured = environmentsQuery.error.message === "Account Pool Manager is not configured";
      return (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-6">
          <p className="font-medium text-destructive">
            {managerNotConfigured ? t("accountPool.managerNotConfigured") : t("accountPool.loadFailed")}
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            {managerNotConfigured ? t("accountPool.managerNotConfiguredDescription") : environmentsQuery.error.message}
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="mt-4"
            onClick={() => void environmentsQuery.refetch()}
          >
            {t("accountPool.retry")}
          </Button>
        </div>
      );
    }
    if (environments.length === 0) {
      return (
        <div className="rounded-md border border-dashed border-border p-12 text-center">
          <p className="font-medium">{t("accountPool.noEnvironments")}</p>
          <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.noEnvironmentsDescription")}</p>
        </div>
      );
    }
    return (
      <>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {visibleEnvironments.map((environment) => renderCard(environment, statsByCard.get(environment.id)))}
        </div>
        {filteredEnvironments.length === 0 && (
          <div className="rounded-md border border-dashed border-border p-12 text-center">
            <p className="font-medium">{t("accountPool.noMatchingEnvironments")}</p>
            <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.noMatchingEnvironmentsDescription")}</p>
          </div>
        )}
        {pageCount > 1 && (
          <div className="mt-4 flex items-center justify-between gap-3" aria-label={t("accountPool.pagination")}>
            <p className="text-sm text-muted-foreground">
              {t("accountPool.showing", {
                from: (currentPage - 1) * PAGE_SIZE + 1,
                to: Math.min(currentPage * PAGE_SIZE, filteredEnvironments.length),
                count: filteredEnvironments.length,
              })}
            </p>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setPage((current) => Math.max(1, current - 1))}
                disabled={currentPage === 1}
              >
                {t("accountPool.previousPage")}
              </Button>
              <span className="text-sm text-muted-foreground">
                {t("accountPool.page", { current: currentPage, total: pageCount })}
              </span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setPage((current) => Math.min(pageCount, current + 1))}
                disabled={currentPage === pageCount}
              >
                {t("accountPool.nextPage")}
              </Button>
            </div>
          </div>
        )}
      </>
    );
  };

  return (
    <div className="w-full p-6">
      <div className="mx-auto flex max-w-7xl flex-col gap-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">{t("accountPool.title")}</h1>
            <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.description")}</p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Badge variant="outline">{t("accountPool.totalEnvironments", { count: overview.total })}</Badge>
              <Badge variant="outline">{t("accountPool.readyEnvironments", { count: overview.ready })}</Badge>
              <Badge variant="outline">
                {t("accountPool.coolingDownEnvironments", { count: overview.coolingDown })}
              </Badge>
              <Badge variant="outline">{t("accountPool.errorEnvironments", { count: overview.error })}</Badge>
              {overview.awaitingAuthorization > 0 && (
                <Badge variant="secondary">
                  {t("accountPool.awaitingAuthorizationEnvironments", { count: overview.awaitingAuthorization })}
                </Badge>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => void environmentsQuery.refetch()}
              disabled={environmentsQuery.isFetching}
              aria-label={t("accountPool.refresh")}
              title={t("accountPool.refresh")}
            >
              <RefreshCw className={environmentsQuery.isFetching ? "animate-spin" : undefined} />
            </Button>
          </div>
        </div>

        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList variant="line" className="h-auto w-full justify-start rounded-none border-b p-0">
            <TabsTrigger value="dashboard" className="flex-none rounded-none px-4 py-2">
              {t("accountPool.tabs.dashboard")}
            </TabsTrigger>
            <TabsTrigger value="providers" className="flex-none rounded-none px-4 py-2">
              {t("accountPool.tabs.providers")}
            </TabsTrigger>
            <TabsTrigger value="oauth" className="flex-none rounded-none px-4 py-2">
              {t("accountPool.tabs.oauth")}
            </TabsTrigger>
            <TabsTrigger value="credentials" className="flex-none rounded-none px-4 py-2">
              {t("accountPool.tabs.credentials")}
            </TabsTrigger>
            <TabsTrigger value="quotas" className="flex-none rounded-none px-4 py-2">
              {t("accountPool.tabs.quotas")}
            </TabsTrigger>
            <TabsTrigger value="settings" className="flex-none rounded-none px-4 py-2">
              {t("accountPool.tabs.settings")}
            </TabsTrigger>
            <TabsTrigger value="proxy-layer" className="flex-none rounded-none px-4 py-2">
              {t("accountPool.tabs.proxyLayer")}
            </TabsTrigger>
            <TabsTrigger value="logs" className="flex-none rounded-none px-4 py-2">
              {t("accountPool.tabs.logs")}
            </TabsTrigger>
            <TabsTrigger value="plugins" className="flex-none rounded-none px-4 py-2">
              {t("accountPool.tabs.plugins")}
            </TabsTrigger>
          </TabsList>
          <TabsContent value="dashboard" className="pt-4">
            {environmentsQuery.isLoading || environmentsQuery.isError ? (
              renderContent()
            ) : (
              <AccountPoolDashboard
                environments={environments}
                statsByCard={statsByCard}
                statsLoading={statsLoading}
                renderCard={renderCard}
              />
            )}
          </TabsContent>
          <TabsContent value="providers" className="pt-4">
            <AccountPoolProviderFamilies accessToken={accessToken} onCreate={openCreateDialog} />
            {accessToken && environments.length > 0 && (
              <div className="mb-4">
                <AccountPoolBatchPanel accessToken={accessToken} environments={environments} policies={policies} />
              </div>
            )}
            {showAccountFilters && (
              <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,1fr)_13rem]">
                <Input
                  value={search}
                  onChange={(event) => {
                    setSearch(event.target.value);
                    setPage(1);
                  }}
                  placeholder={t("accountPool.search")}
                  aria-label={t("accountPool.search")}
                />
                <Select
                  value={statusFilter}
                  onValueChange={(value) => {
                    if (STATUS_FILTERS.some((filter) => filter === value)) {
                      setStatusFilter(value as "all" | AccountPoolStatus);
                      setPage(1);
                    }
                  }}
                >
                  <SelectTrigger aria-label={t("accountPool.filterByStatus")} className="w-full">
                    <SelectValue placeholder={t("accountPool.status.all")} />
                  </SelectTrigger>
                  <SelectContent>
                    {STATUS_FILTERS.map((filter) => (
                      <SelectItem key={filter} value={filter}>
                        {t(`accountPool.status.${filter}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            {renderContent()}
          </TabsContent>
          <TabsContent value="proxy-layer" className="pt-4">
            <ProxyManagerPanel accessToken={accessToken} enabled={canManage} />
          </TabsContent>
          <TabsContent value="oauth" className="pt-4">
            <AccountPoolAuthorizationOverview
              environments={environments}
              onCreate={openCreateDialog}
              onAuthorize={(environment) => authorizeMutation.mutate(environment)}
            />
          </TabsContent>
          <TabsContent value="credentials" className="pt-4">
            <AccountPoolCredentialsPanel accessToken={accessToken} environments={environments} />
          </TabsContent>
          <TabsContent value="quotas" className="pt-4">
            <AccountPoolQuotaPanel
              environments={environments}
              onRefresh={() => {
                if (accessToken) void refreshAccountPoolQuotas(accessToken).then((result) => {
                  if (result.failed_card_ids.length) toast.error(t("accountPool.quotas.partialFailure", { count: result.failed_card_ids.length }));
                  else toast.success(t("accountPool.quotas.refreshed"));
                  return environmentsQuery.refetch();
                }).catch((error: unknown) => toast.fromError(error));
              }}
              refreshing={environmentsQuery.isFetching}
            />
          </TabsContent>
          <TabsContent value="settings" className="pt-4">
            {accessToken && <AccountPoolSettingsPanel accessToken={accessToken} environments={environments} />}
          </TabsContent>
          <TabsContent value="logs" className="pt-4">
            {accessToken && (
              <AccountPoolLogsPanel
                key={logCardId ?? "all"}
                accessToken={accessToken}
                environments={environments}
                initialCardId={logCardId}
              />
            )}
          </TabsContent>
          <TabsContent value="plugins" className="pt-4">
            {accessToken && <AccountPoolPluginsPanel accessToken={accessToken} />}
          </TabsContent>
        </Tabs>
      </div>
      {createOpen && (
        <AccountPoolCreateDialog
          key={authorization?.environment.id ?? "create"}
          accessToken={accessToken}
          initialAuthorization={authorization}
          initialSupplier={createSupplier}
          environments={environments}
          open
          onOpenChange={(open) => {
            setCreateOpen(open);
            if (!open) setAuthorization(null);
          }}
          onCreated={() => void environmentsQuery.refetch()}
        />
      )}
      {configEnvironment && (
        <AccountPoolConfigDialog
          key={configEnvironment.id}
          accessToken={accessToken}
          environment={configEnvironment}
          open
          onOpenChange={(open) => !open && setConfigEnvironment(null)}
          onRefresh={() => void environmentsQuery.refetch()}
          onSaved={() => {
            setConfigEnvironment(null);
            void environmentsQuery.refetch();
          }}
        />
      )}
      {keyEnvironment && accessToken && (
        <AccountPoolKeyDialog
          accessToken={accessToken}
          cardId={keyEnvironment.id}
          name={keyEnvironment.name}
          onClose={() => setKeyEnvironment(null)}
        />
      )}
      {policyEnvironment && accessToken && (
        <AccountPoolPolicyDialog
          accessToken={accessToken}
          cardId={policyEnvironment.id}
          name={policyEnvironment.name}
          supplier={policyEnvironment.supplier}
          onClose={() => {
            setPolicyEnvironment(null);
            void policiesQuery.refetch();
          }}
        />
      )}
      <AlertDialog
        open={deleteEnvironment !== null}
        onOpenChange={(open) => {
          if (!open && !deleteMutation.isPending) setDeleteEnvironment(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("accountPool.confirmDeleteTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteEnvironment
                ? t("accountPool.confirmDeleteDescription", { name: deleteEnvironment.name })
                : t("accountPool.confirmDeleteUnavailable")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>{t("accountPool.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deleteMutation.isPending || deleteEnvironment === null}
              onClick={() => {
                if (deleteEnvironment) deleteMutation.mutate(deleteEnvironment);
              }}
            >
              {deleteMutation.isPending ? t("accountPool.deleting") : t("accountPool.confirmDelete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
