/** 本文件提供号池管理首页，编排数据查询、卡片操作、创建授权和配置弹窗。 */

"use client";

import { accountPoolQueryKeys } from "@/features/account-pool/hooks/accountPoolQueryKeys";
import { getAccountPoolSettings } from "@/features/account-pool/api/AccountPoolManagementApi";
import dynamic from "next/dynamic";
import ModuleLoading from "@/components/shared/ModuleLoading";

const AccountPoolAutomationPanel = dynamic(() =>
  import("@/features/account-pool/components/onboarding/AccountPoolAutomationPanel").then(
    (module) => module.AccountPoolAutomationPanel,
  ),
);

import {
  loadAccountPoolSettingsOverview,
  loadAccountPoolCredentialsPanel,
  loadAccountPoolOnboardingPanel,
  loadAccountPoolUpstreamSyncPanel,
  loadAccountPoolPluginsPanel,
  loadAccountPoolReleasesPanel,
  loadRuntimeConfigDialog,
  loadAccountPoolCreateDialog,
  loadProxyManagerPanel,
  ACCOUNT_POOL_MODULE_LOADERS,
} from "@/features/account-pool/preloadModules";

const AccountPoolSettingsOverview = dynamic(loadAccountPoolSettingsOverview, { loading: ModuleLoading });
const AccountPoolCredentialsPanel = dynamic(loadAccountPoolCredentialsPanel, { loading: ModuleLoading });
const AccountPoolOnboardingPanel = dynamic(loadAccountPoolOnboardingPanel, { loading: ModuleLoading });
const AccountPoolUpstreamSyncPanel = dynamic(loadAccountPoolUpstreamSyncPanel, { loading: ModuleLoading });
const AccountPoolPluginsPanel = dynamic(loadAccountPoolPluginsPanel, { loading: ModuleLoading });
const AccountPoolReleasesPanel = dynamic(loadAccountPoolReleasesPanel, { loading: ModuleLoading });
const RuntimeConfigDialog = dynamic(loadRuntimeConfigDialog, { loading: ModuleLoading });
const AccountPoolCreateDialog = dynamic(loadAccountPoolCreateDialog, { loading: ModuleLoading });
const ProxyManagerPanel = dynamic(loadProxyManagerPanel, { loading: ModuleLoading });

import { Boxes, KeyRound, ListChecks, RefreshCw } from "lucide-react";
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
  AccountPoolSupplier,
} from "@/features/account-pool/utils/AccountPoolTypes";
import { summarizeAccountPoolEnvironments } from "@/features/account-pool/utils/accountPoolSelectors";
import { AccountPoolWorkspace } from "@/features/account-pool/components/shared/AccountPoolWorkspace";
import { useAccountPoolMutations } from "@/features/account-pool/hooks/useAccountPoolMutations";
import { useAccountPoolQuery } from "@/features/account-pool/hooks/useAccountPoolQuery";
import {
  useProxyGatewayQuery,
  useProxyGatewayDelays,
  useProxyISPQuery,
} from "@/features/account-pool/hooks/useProxyGateways";

const AccountPoolModelTestDialog = dynamic(
  () =>
    import("@/features/account-pool/components/cards/AccountPoolModelTestDialog").then(
      (module) => module.AccountPoolModelTestDialog,
    ),
  { loading: ModuleLoading },
);
const AccountPoolQuotaResetDialog = dynamic(() =>
  import("@/features/account-pool/components/cards/AccountPoolQuotaResetDialog").then(
    (module) => module.AccountPoolQuotaResetDialog,
  ),
);
const AccountPoolCredentialDialog = dynamic(() =>
  import("@/features/account-pool/components/cards/AccountPoolCredentialDialog").then(
    (module) => module.AccountPoolCredentialDialog,
  ),
);

const POOL_TABS = [
  "dashboard",
  "providers",
  "credentials",
  "onboarding",
  "oauth-automation",
  "proxy-layer",
  "upstream-sync",
  "releases",
  "settings-overview",
];
const ACCOUNT_POOL_DATA_TABS = new Set(["dashboard", "providers", "credentials", "releases"]);
const ACCOUNT_POOL_CARD_TABS = new Set(["dashboard"]);
export default function AccountPoolPage() {
  const { t } = useTranslation();
  const { accessToken, userRole, isViewOnly } = useAuthorized();
  const router = useRouter();
  const searchParams = useSearchParams();
  const legacyTab = searchParams?.get("tab");
  const legacyTabs: Readonly<Record<string, string>> = { oauth: "providers", quotas: "dashboard" };
  const requestedTab = legacyTab ? legacyTabs[legacyTab] ?? legacyTab : "dashboard";
  const [testEnvironmentId, setTestEnvironmentId] = useState<string | null>(null);
  const [resetEnvironment, setResetEnvironment] = useState<AccountPoolEnvironment | null>(null);
  const [refreshCredentialEnvironment, setRefreshCredentialEnvironment] = useState<AccountPoolEnvironment | null>(null);
  const [verifyAccessEnvironment, setVerifyAccessEnvironment] = useState<AccountPoolEnvironment | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [authorization, setAuthorization] = useState<AccountPoolAuthorization | null>(null);
  const [deleteEnvironment, setDeleteEnvironment] = useState<AccountPoolEnvironment | null>(null);
  const [configEnvironment, setConfigEnvironment] = useState<AccountPoolEnvironment | null>(null);
  const [tabSelection, setTabSelection] = useState<{ query: string; value: string } | null>(null);
  const selectedTab = tabSelection?.query === requestedTab ? tabSelection.value : requestedTab;
  const activeTab = POOL_TABS.includes(selectedTab) ? selectedTab : "dashboard";
  const [visitedTabs, setVisitedTabs] = useState<ReadonlySet<string>>(() => new Set([activeTab]));
  if (!visitedTabs.has(activeTab)) setVisitedTabs(new Set([...visitedTabs, activeTab]));
  const setActiveTab = (value: string) => {
    setTabSelection({ query: requestedTab, value });
  };
  const [createSupplier, setCreateSupplier] = useState<AccountPoolSupplier>("openai_codex");
  const [refreshingQuotas, setRefreshingQuotas] = useState(false);
  const canManage = canManageAccountPool(userRole, isViewOnly);
  useEffect(() => {
    if (!canManage || !accessToken) return;
    return schedulePreloads(ACCOUNT_POOL_MODULE_LOADERS);
  }, [accessToken, canManage]);
  const dataTabActive = ACCOUNT_POOL_DATA_TABS.has(activeTab);
  const cardTabActive = ACCOUNT_POOL_CARD_TABS.has(activeTab);
  const dashboardActive = activeTab === "dashboard";
  const environmentsQuery = useAccountPoolQuery(
    accessToken,
    canManage && (dataTabActive || activeTab === "proxy-layer"),
    dataTabActive,
  );
  const quotaRefreshStatusQuery = useQuery({
    ...accountPoolQuotaRefreshStatusOptions(accessToken),
    enabled: canManage && accessToken !== null && dashboardActive,
    refetchInterval: 15_000,
  });
  const gatewaysQuery = useProxyGatewayQuery(accessToken, canManage && cardTabActive);
  const proxySettingsQuery = useQuery({
    queryKey: accountPoolQueryKeys.settings(accessToken),
    queryFn: () => getAccountPoolSettings(accessToken!),
    enabled: canManage && accessToken !== null && cardTabActive,
    staleTime: 30_000,
    retry: false,
  });
  const gatewayDelaysQuery = useProxyGatewayDelays(accessToken, canManage && cardTabActive);
  const proxyISPsQuery = useProxyISPQuery(accessToken, canManage && cardTabActive);
  const policiesQueryOptions = {
    ...accountPoolPolicyOptions(accessToken),
    enabled: canManage && accessToken !== null && (cardTabActive || activeTab === "providers"),
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
  const testEnvironment = environments.find((environment) => environment.id === testEnvironmentId);
  const policies = useMemo(() => policiesQuery.data ?? [], [policiesQuery.data]);
  const gatewaysByProfile = useMemo(
    () => new Map((gatewaysQuery.data ?? []).map((gateway) => [gateway.profile_id, gateway] as const)),
    [gatewaysQuery.data],
  );
  const policiesByCard = useMemo(
    () => new Map((policiesQuery.data ?? []).map((item) => [item.card_id, item] as const)),
    [policiesQuery.data],
  );
  const [statsDays, setStatsDays] = useState(30);
  const [compactNumbers, setCompactNumbers] = useState(true);
  const dashboardStatsQuery = useQuery({
    ...accountPoolDashboardStatsOptions(accessToken, statsDays),
    enabled: canManage && accessToken !== null && cardTabActive,
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
      const profileId =
        environment.proxy_mode === "default_gateway"
          ? proxySettingsQuery.data?.values.default_proxy_profile_id
          : environment.proxy_profile_id;
      const gateway = profileId ? gatewaysByProfile.get(profileId) : undefined;
      return (
        <AccountPoolCard
          key={environment.id}
          environment={environment}
          requestStats={requestStats}
          compactNumbers={compactNumbers}
          proxyGateway={gateway}
          proxyName={proxyISPsQuery.data?.find((profile) => profile.id === profileId)?.name}
          proxyDelay={gatewayDelaysQuery.data?.find((delay) => delay.port === gateway?.port)}
          ispProbe={!gateway ? proxyISPsQuery.data?.find((profile) => profile.id === profileId)?.last_probe : undefined}
          onConfigure={setConfigEnvironment}
          onEnabledChange={handleEnabledChange}
          onAuthorize={handleAuthorize}
          onDelete={setDeleteEnvironment}
          onManageKey={handleManageKey}
          onViewLogs={handleViewLogs}
          onTest={(environment) => setTestEnvironmentId(environment.id)}
          onQuotaReset={setResetEnvironment}
          onRefreshCredentials={setRefreshCredentialEnvironment}
          onVerifyAccess={setVerifyAccessEnvironment}
          tags={policyView?.policy?.tags}
          group={policyView?.policy?.group}
          policy={policyView}
          disabled={busy}
        />
      );
    },
    [
      busy,
      compactNumbers,
      gatewaysByProfile,
      gatewayDelaysQuery.data,
      proxyISPsQuery.data,
      proxySettingsQuery.data,
      handleAuthorize,
      handleEnabledChange,
      handleManageKey,
      handleViewLogs,
      policiesByCard,
    ],
  );
  const isTabVisited = (tab: string) => tab === activeTab || visitedTabs.has(tab);

  if (!canManage) return <AdminOnlyNotice pageTitle={t("accountPool.title")} />;

  const renderContent = () => {
    if (environmentsQuery.isLoading) {
      return (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
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
    return null;
  };

  return (
    <div className="w-full p-4 sm:p-6">
      <div className="mx-auto flex max-w-7xl flex-col gap-4">
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
          <TabsList className="h-auto group-data-horizontal/tabs:h-auto w-full flex-wrap justify-start gap-2 rounded-xl border bg-muted/30 p-2 max-sm:flex-nowrap max-sm:overflow-x-auto">
            <TabsTrigger
              value="dashboard"
              className="h-auto flex-none rounded-lg border-border bg-background px-4 py-2 data-active:border-blue-300 data-active:bg-blue-50 data-active:text-blue-900 dark:data-active:border-blue-700 dark:data-active:bg-blue-950/40 dark:data-active:text-blue-200"
            >
              {t("accountPool.tabs.dashboard")}
            </TabsTrigger>
            <TabsTrigger
              value="providers"
              className="h-auto flex-none rounded-lg border-border bg-background px-4 py-2 data-active:border-blue-300 data-active:bg-blue-50 data-active:text-blue-900 dark:data-active:border-blue-700 dark:data-active:bg-blue-950/40 dark:data-active:text-blue-200"
            >
              提供商与 OAuth
            </TabsTrigger>

            <TabsTrigger
              value="credentials"
              className="h-auto flex-none rounded-lg border-border bg-background px-4 py-2 data-active:border-blue-300 data-active:bg-blue-50 data-active:text-blue-900 dark:data-active:border-blue-700 dark:data-active:bg-blue-950/40 dark:data-active:text-blue-200"
            >
              {t("accountPool.tabs.credentials")}
            </TabsTrigger>
            <TabsTrigger
              value="onboarding"
              className="h-auto flex-none rounded-lg border-border bg-background px-4 py-2 data-active:border-blue-300 data-active:bg-blue-50 data-active:text-blue-900 dark:data-active:border-blue-700 dark:data-active:bg-blue-950/40 dark:data-active:text-blue-200"
            >
              自动化上号
            </TabsTrigger>
            <TabsTrigger
              value="oauth-automation"
              className="h-auto flex-none rounded-lg border-border bg-background px-4 py-2 data-active:border-blue-300 data-active:bg-blue-50 data-active:text-blue-900 dark:data-active:border-blue-700 dark:data-active:bg-blue-950/40 dark:data-active:text-blue-200"
            >
              授权自动化
            </TabsTrigger>

            <TabsTrigger
              value="proxy-layer"
              className="h-auto flex-none rounded-lg border-border bg-background px-4 py-2 data-active:border-blue-300 data-active:bg-blue-50 data-active:text-blue-900 dark:data-active:border-blue-700 dark:data-active:bg-blue-950/40 dark:data-active:text-blue-200"
            >
              {t("accountPool.tabs.proxyLayer")}
            </TabsTrigger>
            <TabsTrigger
              value="upstream-sync"
              className="h-auto flex-none rounded-lg border-border bg-background px-4 py-2 data-active:border-blue-300 data-active:bg-blue-50 data-active:text-blue-900 dark:data-active:border-blue-700 dark:data-active:bg-blue-950/40 dark:data-active:text-blue-200"
            >
              {t("accountPool.tabs.upstreamSync")}
            </TabsTrigger>
            <TabsTrigger
              value="releases"
              className="h-auto flex-none rounded-lg border-border bg-background px-4 py-2 data-active:border-blue-300 data-active:bg-blue-50 data-active:text-blue-900 dark:data-active:border-blue-700 dark:data-active:bg-blue-950/40 dark:data-active:text-blue-200"
            >
              版本管理
            </TabsTrigger>
            <TabsTrigger
              value="settings-overview"
              className="h-auto flex-none rounded-lg border-border bg-background px-4 py-2 data-active:border-blue-300 data-active:bg-blue-50 data-active:text-blue-900 dark:data-active:border-blue-700 dark:data-active:bg-blue-950/40 dark:data-active:text-blue-200"
            >
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
                accessToken={accessToken}
                statsDays={statsDays}
                onStatsDaysChange={setStatsDays}
                compactNumbers={compactNumbers}
                onCompactNumbersChange={setCompactNumbers}
                environments={environments}
                statsByCard={statsByCard}
                statsLoading={statsLoading}
                standardSummary={dashboardStatsQuery.data?.summary}
                gatewayLatency={dashboardStatsQuery.data?.gateway_latency}
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
            <AccountPoolWorkspace
              key={legacyTab === "oauth" ? "oauth" : "providers"}
              title="提供商与 OAuth"
              defaultValue={legacyTab === "oauth" ? "oauth" : "catalog"}
              defaultOpen
              items={[
                {
                  value: "catalog",
                  icon: Boxes,
                  label: "AI 提供商",
                  summary: "认证方式与新建",
                  content: <AccountPoolProviderFamilies accessToken={accessToken} onCreate={openCreateDialog} />,
                },
                {
                  value: "oauth",
                  icon: KeyRound,
                  label: "OAuth 登录",
                  summary: `${environments.filter((environment) => environment.authorization_flow !== "direct_credential").length} 张卡片`,
                  content: (
                    <AccountPoolAuthorizationOverview
                      accessToken={accessToken}
                      active={activeTab === "providers"}
                      environments={environments}
                      onCreate={openCreateDialog}
                      onAuthorize={handleAuthorize}
                    />
                  ),
                },
                {
                  value: "batch",
                  icon: ListChecks,
                  label: "批量操作",
                  summary: `${environments.length} 张卡片`,
                  content: accessToken && (
                    <AccountPoolBatchPanel accessToken={accessToken} environments={environments} policies={policies} />
                  ),
                },
              ]}
            />
          </DeferredTabPanel>
          <DeferredTabPanel
            value="proxy-layer"
            className="pt-4"
            active={activeTab === "proxy-layer"}
            visited={isTabVisited("proxy-layer")}
          >
            <ProxyManagerPanel
              accessToken={accessToken}
              enabled={canManage}
              environments={environments}
              environmentsLoading={environmentsQuery.isLoading || environmentsQuery.isError}
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
            value="oauth-automation"
            className="pt-4"
            active={activeTab === "oauth-automation"}
            visited={isTabVisited("oauth-automation")}
          >
            {accessToken && <AccountPoolAutomationPanel accessToken={accessToken} />}
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
            {accessToken && (
              <AccountPoolWorkspace
                title="上游同步"
                items={[
                  {
                    value: "cliproxyapi",
                    label: "CLIProxyAPI",
                    content: <AccountPoolUpstreamSyncPanel accessToken={accessToken} target="cliproxyapi" />,
                  },
                  {
                    value: "litellm",
                    label: "LiteLLM",
                    content: <AccountPoolUpstreamSyncPanel accessToken={accessToken} target="litellm" />,
                  },
                ]}
              />
            )}
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
      {testEnvironment && accessToken && (
        <AccountPoolModelTestDialog
          key={testEnvironment.id}
          accessToken={accessToken}
          environment={testEnvironment}
          onClose={() => setTestEnvironmentId(null)}
        />
      )}
      {resetEnvironment && accessToken && (
        <AccountPoolQuotaResetDialog
          key={resetEnvironment.id}
          accessToken={accessToken}
          environment={resetEnvironment}
          onClose={() => setResetEnvironment(null)}
        />
      )}
      {refreshCredentialEnvironment && accessToken && (
        <AccountPoolCredentialDialog
          key={refreshCredentialEnvironment.id}
          mode="refresh"
          accessToken={accessToken}
          environment={refreshCredentialEnvironment}
          onClose={() => setRefreshCredentialEnvironment(null)}
        />
      )}
      {verifyAccessEnvironment && accessToken && (
        <AccountPoolCredentialDialog
          key={verifyAccessEnvironment.id}
          mode="verify"
          accessToken={accessToken}
          environment={verifyAccessEnvironment}
          onClose={() => setVerifyAccessEnvironment(null)}
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
