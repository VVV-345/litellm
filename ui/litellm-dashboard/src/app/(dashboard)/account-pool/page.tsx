/** 本文件提供号池管理首页，编排数据查询、卡片操作、创建授权和配置弹窗。 */

"use client";

import dynamic from "next/dynamic";
import ModuleLoading from "@/components/shared/ModuleLoading";

import {
  loadAccountPoolSettingsOverview,
  loadAccountPoolCredentialsPanel,
  loadAccountPoolOnboardingPanel,
  loadAccountPoolQuotaPanel,
  loadAccountPoolUpstreamSyncPanel,
  loadAccountPoolPluginsPanel,
  loadAccountPoolReleasesPanel,
  loadRuntimeSettingsSection,
  loadRuntimeConfigDialog,
  loadRuntimePolicyDialog,
  loadAccountPoolCreateDialog,
  loadProxyManagerPanel,
  ACCOUNT_POOL_MODULE_LOADERS,
} from "@/features/account-pool/preloadModules";

const AccountPoolSettingsOverview = dynamic(loadAccountPoolSettingsOverview, { loading: ModuleLoading });
const AccountPoolCredentialsPanel = dynamic(loadAccountPoolCredentialsPanel, { loading: ModuleLoading });
const AccountPoolOnboardingPanel = dynamic(loadAccountPoolOnboardingPanel, { loading: ModuleLoading });
const AccountPoolQuotaPanel = dynamic(loadAccountPoolQuotaPanel, { loading: ModuleLoading });
const AccountPoolUpstreamSyncPanel = dynamic(loadAccountPoolUpstreamSyncPanel, { loading: ModuleLoading });
const AccountPoolPluginsPanel = dynamic(loadAccountPoolPluginsPanel, { loading: ModuleLoading });
const AccountPoolReleasesPanel = dynamic(loadAccountPoolReleasesPanel, { loading: ModuleLoading });
const RuntimeSettingsSection = dynamic(loadRuntimeSettingsSection, { loading: ModuleLoading });
const RuntimeConfigDialog = dynamic(loadRuntimeConfigDialog, { loading: ModuleLoading });
const RuntimePolicyDialog = dynamic(loadRuntimePolicyDialog, { loading: ModuleLoading });
const AccountPoolCreateDialog = dynamic(loadAccountPoolCreateDialog, { loading: ModuleLoading });
const ProxyManagerPanel = dynamic(loadProxyManagerPanel, { loading: ModuleLoading });

import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { DeferredTabPanel } from "@/components/shared/DeferredTabPanel";
import { schedulePreloads } from "@/lib/progressivePreload";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";

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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "@/lib/toast";
import { migratedHref } from "@/utils/migratedPages";

import { AccountPoolCard } from "@/features/account-pool/components/cards/AccountPoolCard";
import { AccountPoolBatchPanel } from "@/features/account-pool/components/cards/AccountPoolBatchPanel";
import { AccountPoolDashboard } from "@/features/account-pool/components/dashboard/AccountPoolDashboard";
import { AccountPoolAuthorizationOverview } from "@/features/account-pool/components/credentials/AccountPoolAuthorizationOverview";
import {
  accountPoolDashboardStatsOptions,
  accountPoolPolicyOptions,
  accountPoolQuotaRefreshStatusOptions,
} from "@/features/account-pool/hooks/accountPoolOptions";
import { AccountPoolProviderFamilies } from "@/features/account-pool/components/providers/AccountPoolProviderFamilies";
import { refreshAccountPoolQuotas, type ErrorStats } from "@/features/account-pool/api/AccountPoolManagementApi";
import { canManageAccountPool } from "@/features/account-pool/utils/AccountPoolPermissions";
import type {
  AccountPoolAuthorization,
  AccountPoolEnvironment,
  AccountPoolStatus,
  AccountPoolSupplier,
} from "@/features/account-pool/utils/AccountPoolTypes";
import {
  filterAccountPoolEnvironments,
  paginateAccountPoolEnvironments,
  summarizeAccountPoolEnvironments,
} from "@/features/account-pool/utils/accountPoolSelectors";
import { useAccountPoolMutations } from "@/features/account-pool/hooks/useAccountPoolMutations";
import { useAccountPoolQuery } from "@/features/account-pool/hooks/useAccountPoolQuery";
import { useProxyGatewayQuery } from "@/features/account-pool/hooks/useProxyGateways";

const PAGE_SIZE = 24;
const POOL_TABS = [
  "dashboard",
  "providers",
  "oauth",
  "credentials",
  "onboarding",
  "quotas",
  "proxy-layer",
  "upstream-sync",
  "releases",
  "settings-overview",
];
const ACCOUNT_POOL_DATA_TABS = new Set(["dashboard", "providers", "oauth", "credentials", "quotas", "releases"]);
const ACCOUNT_POOL_CARD_TABS = new Set(["dashboard", "providers"]);
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
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedTab = searchParams?.get("tab") ?? "dashboard";
  const [createOpen, setCreateOpen] = useState(false);
  const [authorization, setAuthorization] = useState<AccountPoolAuthorization | null>(null);
  const [deleteEnvironment, setDeleteEnvironment] = useState<AccountPoolEnvironment | null>(null);
  const [configEnvironment, setConfigEnvironment] = useState<AccountPoolEnvironment | null>(null);
  const [policyEnvironment, setPolicyEnvironment] = useState<AccountPoolEnvironment | null>(null);
  const [tabSelection, setTabSelection] = useState<{ query: string; value: string } | null>(null);
  const selectedTab = tabSelection?.query === requestedTab ? tabSelection.value : requestedTab;
  const activeTab = POOL_TABS.includes(selectedTab) ? selectedTab : "dashboard";
  const [visitedTabs, setVisitedTabs] = useState<ReadonlySet<string>>(() => new Set([activeTab]));
  if (!visitedTabs.has(activeTab)) setVisitedTabs(new Set([...visitedTabs, activeTab]));
  const setActiveTab = (value: string) => {
    setTabSelection({ query: requestedTab, value });
  };
  const [createSupplier, setCreateSupplier] = useState<AccountPoolSupplier>("openai_codex");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | AccountPoolStatus>("all");
  const [page, setPage] = useState(1);
  const [refreshingQuotas, setRefreshingQuotas] = useState(false);
  const canManage = canManageAccountPool(userRole, isViewOnly);
  useEffect(() => {
    if (!canManage || !accessToken) return;
    return schedulePreloads(ACCOUNT_POOL_MODULE_LOADERS);
  }, [accessToken, canManage]);
  const dataTabActive = ACCOUNT_POOL_DATA_TABS.has(activeTab);
  const cardTabActive = ACCOUNT_POOL_CARD_TABS.has(activeTab);
  const dashboardActive = activeTab === "dashboard";
  const environmentsQuery = useAccountPoolQuery(accessToken, canManage && dataTabActive, dataTabActive);
  const quotaRefreshStatusQuery = useQuery({
    ...accountPoolQuotaRefreshStatusOptions(accessToken),
    enabled: canManage && accessToken !== null && dashboardActive,
    refetchInterval: 15_000,
  });
  const gatewaysQuery = useProxyGatewayQuery(accessToken, canManage && cardTabActive);
  const policiesQueryOptions = {
    ...accountPoolPolicyOptions(accessToken),
    enabled: canManage && accessToken !== null && cardTabActive,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
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
  const gatewaysByProfile = useMemo(
    () => new Map((gatewaysQuery.data ?? []).map((gateway) => [gateway.profile_id, gateway] as const)),
    [gatewaysQuery.data],
  );
  const policiesByCard = useMemo(
    () => new Map((policiesQuery.data ?? []).map((item) => [item.card_id, item] as const)),
    [policiesQuery.data],
  );
  const dashboardStatsQuery = useQuery({
    ...accountPoolDashboardStatsOptions(accessToken),
    enabled: canManage && accessToken !== null && dashboardActive,
    refetchInterval: 15_000,
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
  const statsLoading = dashboardStatsQuery.isPending;
  const filteredEnvironments = useMemo(
    () => filterAccountPoolEnvironments(environments, search, statusFilter, policies),
    [environments, policies, search, statusFilter],
  );
  const pageCount = Math.max(1, Math.ceil(filteredEnvironments.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const visibleEnvironments = paginateAccountPoolEnvironments(filteredEnvironments, currentPage, PAGE_SIZE);
  const overview = useMemo(() => summarizeAccountPoolEnvironments(environments), [environments]);
  const busy = updateMutation.isPending || deleteMutation.isPending || authorizeMutation.isPending;
  const refreshQuotas = () => {
    if (!accessToken || refreshingQuotas) return;
    setRefreshingQuotas(true);
    void refreshAccountPoolQuotas(accessToken)
      .then((result) => {
        if (result.failed_card_ids.length)
          toast.error(t("accountPool.quotas.partialFailure", { count: result.failed_card_ids.length }));
        else toast.success(t("accountPool.quotas.refreshed"));
        return Promise.all([environmentsQuery.refetch(), quotaRefreshStatusQuery.refetch()]);
      })
      .catch((error: unknown) => toast.fromError(error))
      .finally(() => setRefreshingQuotas(false));
  };
  const closePolicy = () => {
    setPolicyEnvironment(null);
    void policiesQuery.refetch();
    void environmentsQuery.refetch();
  };
  const showAccountFilters = !environmentsQuery.isLoading && !environmentsQuery.isError && environments.length > 0;

  const openCreateDialog = (supplier: AccountPoolSupplier = "openai_codex") => {
    setAuthorization(null);
    setCreateSupplier(supplier);
    setCreateOpen(true);
  };

  const { mutate: updateEnvironment } = updateMutation;
  const { mutate: authorizeEnvironment } = authorizeMutation;
  const handleEnabledChange = useCallback(
    (environment: AccountPoolEnvironment, enabled: boolean) => updateEnvironment({ environment, enabled }),
    [updateEnvironment],
  );
  const handleAuthorize = useCallback(
    (environment: AccountPoolEnvironment) => authorizeEnvironment(environment),
    [authorizeEnvironment],
  );
  const handleManageKey = useCallback(
    (environment: AccountPoolEnvironment) =>
      router.push(`${migratedHref("api-keys")}?create=true&account_id=${encodeURIComponent(environment.id)}`),
    [router],
  );
  const handleViewLogs = useCallback(
    (environment: AccountPoolEnvironment) =>
      router.push(`${migratedHref("logs")}?account_id=${encodeURIComponent(environment.id)}`),
    [router],
  );
  const renderCard = useCallback(
    (environment: AccountPoolEnvironment, requestStats?: ErrorStats) => {
      const policyView = policiesByCard.get(environment.id);
      return (
        <AccountPoolCard
          key={environment.id}
          environment={environment}
          requestStats={requestStats}
          proxyGateway={environment.proxy_profile_id ? gatewaysByProfile.get(environment.proxy_profile_id) : undefined}
          onConfigure={setConfigEnvironment}
          onEnabledChange={handleEnabledChange}
          onAuthorize={handleAuthorize}
          onDelete={setDeleteEnvironment}
          onManageKey={handleManageKey}
          onManagePolicy={setPolicyEnvironment}
          onViewLogs={handleViewLogs}
          tags={policyView?.policy?.tags}
          group={policyView?.policy?.group}
          policy={policyView}
          disabled={busy}
        />
      );
    },
    [busy, gatewaysByProfile, handleAuthorize, handleEnabledChange, handleManageKey, handleViewLogs, policiesByCard],
  );
  const isTabVisited = (tab: string) => tab === activeTab || visitedTabs.has(tab);

  if (!canManage) return <AdminOnlyNotice pageTitle={t("accountPool.title")} />;

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
              onClick={() => {
                void environmentsQuery.refetch();
                void dashboardStatsQuery.refetch();
              }}
              disabled={environmentsQuery.isFetching}
              aria-label={t("accountPool.refresh")}
              title={t("accountPool.refresh")}
            >
              <RefreshCw className={environmentsQuery.isFetching ? "animate-spin" : undefined} />
            </Button>
          </div>
        </div>

        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList variant="line" className="h-auto w-full flex-wrap justify-start rounded-none border-b p-0">
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
            <TabsTrigger value="onboarding" className="flex-none rounded-none px-4 py-2">
              自动化上号
            </TabsTrigger>
            <TabsTrigger value="quotas" className="flex-none rounded-none px-4 py-2">
              {t("accountPool.tabs.quotas")}
            </TabsTrigger>
            <TabsTrigger value="proxy-layer" className="flex-none rounded-none px-4 py-2">
              {t("accountPool.tabs.proxyLayer")}
            </TabsTrigger>
            <TabsTrigger value="upstream-sync" className="flex-none rounded-none px-4 py-2">
              {t("accountPool.tabs.upstreamSync")}
            </TabsTrigger>
            <TabsTrigger value="releases" className="flex-none rounded-none px-4 py-2">
              版本管理
            </TabsTrigger>
            <TabsTrigger value="settings-overview" className="flex-none rounded-none px-4 py-2">
              全局设置总览
            </TabsTrigger>
          </TabsList>
          <DeferredTabPanel
            value="dashboard"
            className="pt-4"
            active={activeTab === "dashboard"}
            visited={isTabVisited("dashboard")}
          >
            {environmentsQuery.isLoading || environmentsQuery.isError ? (
              renderContent()
            ) : (
              <AccountPoolDashboard
                environments={environments}
                statsByCard={statsByCard}
                statsLoading={statsLoading}
                standardSummary={dashboardStatsQuery.data?.summary}
                statsError={dashboardStatsQuery.isError}
                renderCard={renderCard}
                quotaRefreshStatus={quotaRefreshStatusQuery.data ?? null}
                onRefreshQuotas={refreshQuotas}
                refreshingQuotas={refreshingQuotas}
              />
            )}
          </DeferredTabPanel>
          <DeferredTabPanel
            value="providers"
            className="pt-4"
            active={activeTab === "providers"}
            visited={isTabVisited("providers")}
          >
            <AccountPoolProviderFamilies accessToken={accessToken} onCreate={openCreateDialog} />
            {accessToken && (
              <details
                className="my-4 rounded-lg border p-4"
                open={searchParams?.get("section") === "common" || undefined}
              >
                <summary>新卡片默认配置</summary>
                <RuntimeSettingsSection accessToken={accessToken} environments={environments} category="common" />
              </details>
            )}
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
          </DeferredTabPanel>
          <DeferredTabPanel
            value="proxy-layer"
            className="pt-4"
            active={activeTab === "proxy-layer"}
            visited={isTabVisited("proxy-layer")}
          >
            <ProxyManagerPanel accessToken={accessToken} enabled={canManage} />
          </DeferredTabPanel>
          <DeferredTabPanel
            value="oauth"
            className="pt-4"
            active={activeTab === "oauth"}
            visited={isTabVisited("oauth")}
          >
            <AccountPoolAuthorizationOverview
              environments={environments}
              onCreate={openCreateDialog}
              onAuthorize={handleAuthorize}
            />
          </DeferredTabPanel>
          <DeferredTabPanel
            value="credentials"
            className="pt-4"
            active={activeTab === "credentials"}
            visited={isTabVisited("credentials")}
          >
            <AccountPoolCredentialsPanel accessToken={accessToken} environments={environments} />
          </DeferredTabPanel>
          <DeferredTabPanel
            value="onboarding"
            className="pt-4"
            active={activeTab === "onboarding"}
            visited={isTabVisited("onboarding")}
          >
            {accessToken && <AccountPoolOnboardingPanel accessToken={accessToken} />}
          </DeferredTabPanel>
          <DeferredTabPanel
            value="quotas"
            className="pt-4"
            active={activeTab === "quotas"}
            visited={isTabVisited("quotas")}
          >
            {accessToken && (
              <details
                className="mb-4 rounded-lg border p-4"
                open={searchParams?.get("section") === "quota" || undefined}
              >
                <summary>配额耗尽策略</summary>
                <RuntimeSettingsSection accessToken={accessToken} environments={environments} category="quota" />
              </details>
            )}
            <AccountPoolQuotaPanel
              accessToken={accessToken}
              environments={environments}
              onRefresh={refreshQuotas}
              refreshing={refreshingQuotas}
            />
          </DeferredTabPanel>
          <DeferredTabPanel
            value="releases"
            className="pt-4"
            active={activeTab === "releases"}
            visited={isTabVisited("releases")}
          >
            {accessToken && (
              <>
                <AccountPoolReleasesPanel accessToken={accessToken} />
                <details
                  className="mt-6 rounded-lg border p-4"
                  open={searchParams?.get("section") === "plugins" || undefined}
                >
                  <summary>运行插件管理</summary>
                  <AccountPoolPluginsPanel accessToken={accessToken} environments={environments} />
                </details>
              </>
            )}
          </DeferredTabPanel>
          <DeferredTabPanel
            value="upstream-sync"
            className="pt-4"
            active={activeTab === "upstream-sync"}
            visited={isTabVisited("upstream-sync")}
          >
            {accessToken && <AccountPoolUpstreamSyncPanel accessToken={accessToken} />}
          </DeferredTabPanel>
          <DeferredTabPanel
            value="settings-overview"
            className="pt-4"
            active={activeTab === "settings-overview"}
            visited={isTabVisited("settings-overview")}
          >
            {accessToken && <AccountPoolSettingsOverview accessToken={accessToken} />}
          </DeferredTabPanel>
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
        <RuntimeConfigDialog
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
      {policyEnvironment && accessToken && (
        <RuntimePolicyDialog
          key={policyEnvironment.id}
          accessToken={accessToken}
          environment={policyEnvironment}
          environments={environments}
          policies={policies}
          onOpenRuntimeConfig={() => {
            setConfigEnvironment(policyEnvironment);
            setPolicyEnvironment(null);
          }}
          onClose={closePolicy}
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
