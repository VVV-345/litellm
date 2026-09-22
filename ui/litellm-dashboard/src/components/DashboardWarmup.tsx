"use client";

import { useQueryClient, type FetchQueryOptions, type QueryClient, type QueryKey } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";

import { useAuth } from "@/contexts/AuthContext";
import { usePluginMode } from "@/contexts/PluginModeContext";
import { canManageAccountPool } from "@/features/account-pool/utils/AccountPoolPermissions";
import {
  accountPoolDashboardStatsOptions,
  accountPoolEnvironmentOptions,
  accountPoolPolicyOptions,
  accountPoolQuotaRefreshStatusOptions,
} from "@/features/account-pool/hooks/accountPoolOptions";
import { proxyGatewayQueryOptions } from "@/features/account-pool/hooks/useProxyGateways";
import { allTeamsQueryOptions, teamsQueryOptions } from "@/app/(dashboard)/hooks/teams/useTeams";
import { agentsQueryOptions } from "@/app/(dashboard)/hooks/agents/useAgents";
import { customersQueryOptions } from "@/app/(dashboard)/hooks/customers/useCustomers";
import { currentUserQueryOptions } from "@/app/(dashboard)/hooks/users/useCurrentUser";
import { organizationQueryOptions } from "@/app/(dashboard)/hooks/organizations/useOrganizations";
import { keyListQueryOptions } from "@/app/(dashboard)/hooks/keys/useKeys";
import { modelInfoQueryOptions } from "@/app/(dashboard)/hooks/models/useModels";
import { modelCostMapQueryOptions } from "@/app/(dashboard)/hooks/models/useModelCostMap";
import { uiSettingsQueryOptions } from "@/app/(dashboard)/hooks/uiSettings/useUISettings";
import {
  usageDailyActivityQueryOptions,
  usageGatewayActivityQueryOptions,
  usageTagsQueryOptions,
} from "@/app/(dashboard)/hooks/usage/useUsageQueries";
import { migratedHref, legacyPageHref, MIGRATED_PAGES } from "@/utils/migratedPages";
import { fullLogsQueryOptions } from "@/components/view_logs/fullLogsApi";
import { operationLogsQueryOptions } from "@/components/view_logs/operationLogsApi";
import { fullLogStorage } from "@/components/view_logs/fullLogsApi";
import {
  preloadFullLogModule,
  preloadLogSettingsModule,
  preloadOperationLogModule,
  preloadOtherLogModules,
} from "@/components/view_logs";
import { runPreloadStages, type PreloadStageProgress, type PreloadTask } from "@/lib/progressivePreload";
import { all_admin_roles, rolesAllowedToViewWriteScopedPages } from "@/utils/roles";
import { preloadModelsModules } from "@/app/(dashboard)/models-and-endpoints/preloadModels";
import { preloadAccountPoolModules } from "@/features/account-pool/preloadModules";
import {
  DEFAULT_LOGS_SORTING,
  initialLogsRange,
  requestLogsQueryOptions,
} from "@/components/view_logs/log_filter_logic";
import { dashboardBackgroundTasks } from "@/lib/dashboardBackground";
import { apiClient, modelAvailableCall, userGetInfoV2 } from "@/components/networking";
import { accountPoolQueryKeys } from "@/features/account-pool/hooks/accountPoolQueryKeys";
import { listAccountPoolProviderFamilies } from "@/features/account-pool/api/AccountPoolApi";
import { SESSION_RESET_EVENT } from "@/lib/cacheEvents";

const PRIMARY_PAGES = ["api-keys", "models", "account-pool", "logs", "new_usage"] as const;
const LOG_PAGE_SIZE = 50;
const PRIMARY_REFRESH_MS = 30_000;
const BACKGROUND_REFRESH_MS = 5 * 60_000;

const task = (id: string, run: () => unknown | Promise<unknown>): PreloadTask => ({ id, run });

const pageUrl = (page: string): string => {
  const migratedRoute = MIGRATED_PAGES[page];
  return migratedRoute ? migratedHref(migratedRoute) : legacyPageHref(page);
};

const prefetchPage = async (router: ReturnType<typeof useRouter>, page: string) => {
  await router.prefetch(pageUrl(page));
};

const loadApiKeysPage = async (router: ReturnType<typeof useRouter>) => {
  await Promise.all([prefetchPage(router, "api-keys"), import("@/app/(dashboard)/api-keys/page")]);
};

const loadModelsPage = async (router: ReturnType<typeof useRouter>) => {
  await Promise.all([
    import("@/app/(dashboard)/models-and-endpoints/page"),
    prefetchPage(router, "models"),
    preloadModelsModules(),
  ]);
};

const loadAccountPoolPage = async (router: ReturnType<typeof useRouter>) => {
  const page = await import("@/app/(dashboard)/account-pool/page");
  await prefetchPage(router, "account-pool");
  return page;
};

const loadLogsPage = async (router: ReturnType<typeof useRouter>) => {
  await Promise.all([prefetchPage(router, "logs"), import("@/components/view_logs")]);
};

const loadUsagePage = async (router: ReturnType<typeof useRouter>) => {
  await Promise.all([prefetchPage(router, "new_usage"), import("@/app/(dashboard)/usage/page")]);
};

const runQuery = <TQueryFnData, TError = Error, TData = TQueryFnData, TQueryKey extends QueryKey = QueryKey>(
  queryClient: QueryClient,
  options: FetchQueryOptions<TQueryFnData, TError, TData, TQueryKey>,
  priority: "primary" | "background" = "primary",
) => {
  queryClient.setQueryDefaults(options.queryKey, { meta: { dashboardWarmup: priority }, gcTime: Infinity });
  return queryClient.fetchQuery({
    ...options,
    gcTime: Infinity,
    meta: { ...options.meta, dashboardWarmup: priority },
  });
};

interface DashboardWarmupContext {
  queryClient: QueryClient;
  router: ReturnType<typeof useRouter>;
  accessToken: string;
  userId: string | null;
  userRole: string;
  token: string;
}

const defaultRequestLogOptions = (context: DashboardWarmupContext, pageIndex: number) =>
  requestLogsQueryOptions({
    accessToken: context.accessToken,
    token: context.token,
    userRole: context.userRole,
    userID: context.userId,
    columnFilters: [],
    activeTab: "request logs",
    isLiveTail: false,
    excludeInternalHealthChecks: sessionStorage.getItem("excludeInternalHealthChecks") === "true",
    ...initialLogsRange(context.queryClient),
    pagination: { pageIndex, pageSize: LOG_PAGE_SIZE },
    isCustomDate: false,
    sorting: DEFAULT_LOGS_SORTING,
  });

const createPrimaryTasks = (
  { queryClient, router, accessToken, userId, userRole, token }: DashboardWarmupContext,
  signal: AbortSignal,
): readonly PreloadTask[] => [
  task("virtual-keys", async () => {
    await loadApiKeysPage(router);
    if (signal.aborted) return;
    await Promise.all([
      runQuery(
        queryClient,
        keyListQueryOptions(accessToken, 1, LOG_PAGE_SIZE, {
          sortBy: "created_at",
          sortOrder: "desc",
          expand: "user",
        }),
      ),
      runQuery(queryClient, allTeamsQueryOptions(accessToken, userId, userRole)),
      runQuery(queryClient, organizationQueryOptions(accessToken, userId, userRole)),
      ...(userId
        ? [
            runQuery(queryClient, {
              queryKey: ["dashboard-key-user", userId],
              queryFn: () => userGetInfoV2(accessToken, userId),
            }),
            runQuery(queryClient, {
              queryKey: ["dashboard-key-models", userId],
              queryFn: () => modelAvailableCall(accessToken, userId, userRole),
            }),
          ]
        : []),
    ]);
  }),
  task("models-and-endpoints", async () => {
    await loadModelsPage(router);
    if (signal.aborted) return;
    if (!userId || !rolesAllowedToViewWriteScopedPages.includes(userRole)) return;
    await Promise.all([
      runQuery(queryClient, modelInfoQueryOptions({ accessToken, userId, userRole })),
      runQuery(queryClient, modelInfoQueryOptions({ accessToken, userId, userRole, excludeAutoRouters: true })),
      runQuery(queryClient, teamsQueryOptions(accessToken, userId, userRole)),
      runQuery(queryClient, modelCostMapQueryOptions()),
      runQuery(queryClient, uiSettingsQueryOptions()),
    ]);
  }),
  task("account-pool", async () => {
    await loadAccountPoolPage(router);
    if (signal.aborted) return;
    if (!canManageAccountPool(userRole, false)) return;
    await Promise.all([
      runQuery(queryClient, accountPoolEnvironmentOptions(accessToken)),
      runQuery(queryClient, accountPoolPolicyOptions(accessToken)),
      runQuery(queryClient, accountPoolDashboardStatsOptions(accessToken)),
      runQuery(queryClient, accountPoolQuotaRefreshStatusOptions(accessToken)),
      runQuery(queryClient, proxyGatewayQueryOptions(accessToken)),
      preloadAccountPoolModules(),
      runQuery(queryClient, {
        queryKey: accountPoolQueryKeys.providerFamilies(accessToken),
        queryFn: () => listAccountPoolProviderFamilies(accessToken),
      }),
    ]);
  }),
  task("logs", async () => {
    await loadLogsPage(router);
    await preloadOperationLogModule();
    if (signal.aborted) return;
    await Promise.all(
      [0, 1, 2].map((pageIndex) =>
        runQuery(
          queryClient,
          defaultRequestLogOptions({ queryClient, router, accessToken, userId, userRole, token }, pageIndex),
        ),
      ),
    );
    if (signal.aborted || !canManageAccountPool(userRole, false)) return;
    const filters = {};
    await Promise.all(
      [0, LOG_PAGE_SIZE, LOG_PAGE_SIZE * 2].map((offset) =>
        runQuery(queryClient, operationLogsQueryOptions({ accessToken, filters, offset, pageSize: LOG_PAGE_SIZE })),
      ),
    );
  }),
  task("usage", async () => {
    await loadUsagePage(router);
    if (signal.aborted) return;
    const endTime = new Date();
    const startTime = new Date(endTime.getTime() - 7 * 24 * 60 * 60 * 1000);
    const isAdmin = all_admin_roles.includes(userRole);
    const effectiveUserId = isAdmin ? null : userId;
    await Promise.all([
      runQuery(queryClient, teamsQueryOptions(accessToken, userId, userRole)),
      runQuery(queryClient, organizationQueryOptions(accessToken, userId, userRole)),
      runQuery(queryClient, currentUserQueryOptions(accessToken, userId)),
      ...(isAdmin ? [runQuery(queryClient, customersQueryOptions(accessToken, userRole))] : []),
      ...(isAdmin ? [runQuery(queryClient, agentsQueryOptions(accessToken, userRole))] : []),
      runQuery(queryClient, usageDailyActivityQueryOptions(accessToken, startTime, endTime, effectiveUserId)),
      runQuery(queryClient, usageTagsQueryOptions(accessToken, startTime, endTime)),
      ...(isAdmin
        ? [runQuery(queryClient, usageGatewayActivityQueryOptions(accessToken, startTime, endTime, true))]
        : []),
    ]);
  }),
];

const batchesOfTwo = (tasks: readonly PreloadTask[]): PreloadTask[][] =>
  Array.from({ length: Math.ceil(tasks.length / 2) }, (_, index) => tasks.slice(index * 2, index * 2 + 2));

const createBackgroundBatches = async (context: DashboardWarmupContext, signal: AbortSignal) => {
  const { queryClient, accessToken, userRole } = context;
  const tasks: PreloadTask[] = [...dashboardBackgroundTasks(context, signal)];
  tasks.push(task("other-log-modules", preloadOtherLogModules));
  if (canManageAccountPool(userRole, false)) {
    tasks.unshift(
      task("full-logs-module", preloadFullLogModule),
      task("log-settings-module", preloadLogSettingsModule),
      task("log-settings", () =>
        runQuery(
          queryClient,
          {
            queryKey: ["logs", "settings", accessToken],
            queryFn: () => apiClient.get("/logs/settings", { accessToken }),
          },
          "background",
        ),
      ),
      task("full-log-storage", () =>
        runQuery(
          queryClient,
          {
            queryKey: ["logs", "full-log-storage", accessToken],
            queryFn: () => fullLogStorage(accessToken),
          },
          "background",
        ),
      ),
    );
    const firstFullPage = await runQuery(queryClient, fullLogsQueryOptions(accessToken, {}), "background").catch(
      () => null,
    );
    if (signal.aborted) return [];
    if (!firstFullPage) tasks.push(task("full-logs", () => Promise.reject(new Error("Full logs are not ready"))));
    for (let offset = LOG_PAGE_SIZE; offset < (firstFullPage?.totals?.attempts ?? 0); offset += LOG_PAGE_SIZE) {
      tasks.push(
        task(`full-logs:${offset}`, () =>
          runQuery(queryClient, fullLogsQueryOptions(accessToken, { offset }), "background"),
        ),
      );
    }
    const first = queryClient.getQueryData<
      Awaited<ReturnType<ReturnType<typeof operationLogsQueryOptions>["queryFn"]>>
    >(operationLogsQueryOptions({ accessToken, filters: {}, offset: 0, pageSize: LOG_PAGE_SIZE }).queryKey);
    for (let offset = LOG_PAGE_SIZE * 3; offset < (first?.total ?? 0); offset += LOG_PAGE_SIZE) {
      tasks.push(
        task(`operation-logs:${offset}`, () =>
          runQuery(
            queryClient,
            operationLogsQueryOptions({ accessToken, filters: {}, offset, pageSize: LOG_PAGE_SIZE }),
            "background",
          ),
        ),
      );
    }
  }
  const requestOptions = defaultRequestLogOptions(context, 0);
  const firstRequestPage = queryClient.getQueryData<
    import("@/components/view_logs/log_filter_logic").PaginatedResponse
  >(requestOptions.queryKey);
  for (let pageIndex = 3; pageIndex < (firstRequestPage?.total_pages ?? 0); pageIndex++) {
    tasks.push(
      task(`request-logs:${pageIndex}`, () =>
        runQuery(queryClient, defaultRequestLogOptions(context, pageIndex), "background"),
      ),
    );
  }
  return batchesOfTwo(tasks);
};

export const refreshWarmupQueries = async (queryClient: QueryClient, primary: boolean, signal: AbortSignal) => {
  const queries = queryClient.getQueryCache().findAll({
    predicate: (query) =>
      query.queryKey[0] !== "dashboard-http" &&
      query.options.queryFn !== undefined &&
      query.meta?.dashboardWarmup === (primary ? "primary" : "background"),
  });
  await queryClient.invalidateQueries({ queryKey: ["dashboard-http"], refetchType: "none" });
  for (const batch of batchesOfTwo(
    queries.map((query) =>
      task(query.queryHash, () => queryClient.refetchQueries({ queryKey: query.queryKey, exact: true })),
    ),
  )) {
    if (signal.aborted || document.hidden) return;
    await Promise.allSettled(batch.map((item) => item.run()));
    await new Promise<void>((resolve) => window.setTimeout(resolve, 250));
  }
};

export const runDashboardWarmup = async (
  context: DashboardWarmupContext,
  onProgress: (progress: PreloadStageProgress) => void,
  signal: AbortSignal,
) => {
  return runPreloadStages(createPrimaryTasks(context, signal), [], {
    onProgress,
    signal,
    loadBackground: () => createBackgroundBatches(context, signal),
  });
};

const progressPercent = (completed: number, total: number): number =>
  total === 0 ? 0 : Math.round((completed / total) * 100);

function WarmupProgress({
  state,
  primaryOnly,
  retry,
}: {
  state: PreloadStageProgress;
  primaryOnly: boolean;
  retry: () => void;
}) {
  const progress = primaryOnly ? state.primary : state.background;
  const succeeded = progress.completed - progress.failed;
  const value = progressPercent(succeeded, progress.total);
  return (
    <div
      className={
        primaryOnly
          ? "flex min-h-screen items-center justify-center bg-background p-6"
          : "fixed bottom-4 right-4 z-floating w-72 rounded-md border bg-background p-3 shadow-sm"
      }
      role="status"
      aria-live="polite"
    >
      <div className={primaryOnly ? "w-full max-w-md space-y-3" : "space-y-2"}>
        <div className="flex items-center justify-between gap-3 text-sm">
          <span>{primaryOnly ? "正在准备主要模块" : "后台加载中"}</span>
          <span>
            {succeeded}/{progress.total}
          </span>
        </div>
        <div
          className="h-2 w-full overflow-hidden rounded-full bg-muted"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={progress.total || 1}
          aria-valuenow={succeeded}
          aria-label={primaryOnly ? "主要模块加载进度" : "后台模块加载进度"}
        >
          <div className="h-full bg-primary transition-[width]" style={{ width: `${value}%` }} />
        </div>
        {progress.failed > 0 && (
          <p className="text-xs text-destructive" role="alert">
            {progress.failed} 个模块加载失败，尚未准备完成
          </p>
        )}
        {progress.failed > 0 && progress.completed === progress.total && <Button onClick={retry}>重试加载</Button>}
      </div>
    </div>
  );
}

export default function DashboardWarmup({ children }: { children: React.ReactNode }) {
  const { accessToken, userID, userRole, token } = useAuth();
  const { mode } = usePluginMode();
  const queryClient = useQueryClient();
  const router = useRouter();
  const [readyKey, setReadyKey] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<PreloadStageProgress>({
    primary: { completed: 0, total: PRIMARY_PAGES.length, failed: 0 },
    background: { completed: 0, total: 0, failed: 0 },
    stage: "primary",
  });

  const shouldWarmup = Boolean(accessToken && mode === "ai-gateway");
  const warmupKey = shouldWarmup ? `${accessToken}:${userID}:${userRole}:${attempt}` : null;
  const ready = !shouldWarmup || readyKey === warmupKey;

  useEffect(() => {
    if (!accessToken || mode !== "ai-gateway") {
      return;
    }
    const controller = new AbortController();
    const stop = () => controller.abort();
    window.addEventListener(SESSION_RESET_EVENT, stop);
    const context: DashboardWarmupContext = {
      queryClient,
      router,
      accessToken,
      userId: userID,
      userRole,
      token: token ?? "",
    };
    void runDashboardWarmup(
      context,
      (next) => {
        if (controller.signal.aborted) return;
        setState(next);
        if (next.stage !== "primary") setReadyKey(warmupKey);
      },
      controller.signal,
    );
    return () => {
      controller.abort();
      window.removeEventListener(SESSION_RESET_EVENT, stop);
    };
  }, [accessToken, mode, queryClient, router, userID, userRole, warmupKey, token, attempt]);

  useEffect(() => {
    if (!accessToken || mode !== "ai-gateway" || readyKey !== warmupKey) return;
    const controller = new AbortController();
    const stop = () => controller.abort();
    window.addEventListener(SESSION_RESET_EVENT, stop);
    let timer: number;
    let lastBackgroundRefresh = Date.now();
    const refresh = async () => {
      try {
        if (document.hidden || controller.signal.aborted) return;
        await refreshWarmupQueries(queryClient, true, controller.signal);
        if (state.stage === "complete" && Date.now() - lastBackgroundRefresh >= BACKGROUND_REFRESH_MS) {
          await refreshWarmupQueries(queryClient, false, controller.signal);
          lastBackgroundRefresh = Date.now();
        }
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(() => void refresh(), PRIMARY_REFRESH_MS);
      }
    };
    timer = window.setTimeout(() => void refresh(), PRIMARY_REFRESH_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
      window.removeEventListener(SESSION_RESET_EVENT, stop);
    };
  }, [accessToken, mode, queryClient, readyKey, warmupKey, state.stage]);

  const retry = () => setAttempt((value) => value + 1);
  if (!ready) return <WarmupProgress state={state} primaryOnly retry={retry} />;
  return (
    <>
      {children}
      {(state.stage === "background" || state.background.failed > 0) && (
        <WarmupProgress state={state} primaryOnly={false} retry={retry} />
      )}
    </>
  );
}
