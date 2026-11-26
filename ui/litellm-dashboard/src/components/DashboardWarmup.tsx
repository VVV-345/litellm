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
import { pageHref } from "@/utils/migratedPages";
import { fullLogSessionsQueryOptions } from "@/components/view_logs/fullLogsApi";
import { operationLogsQueryOptions } from "@/components/view_logs/operationLogsApi";
import { fullLogStorage } from "@/components/view_logs/fullLogsApi";
import {
  preloadFullLogModule,
  preloadLogSettingsModule,
  preloadOperationLogModule,
  preloadOtherLogModules,
} from "@/components/view_logs";
import { runPreloadStages, type PreloadStageProgress, type PreloadTask } from "@/lib/progressivePreload";
import { all_admin_roles, isViewOnlyRole, isViewOnlySessionRole, rolesAllowedToViewWriteScopedPages } from "@/utils/roles";
import { checkTokenValidity, decodeToken } from "@/utils/jwtUtils";
import { preloadModelsModules } from "@/app/(dashboard)/models-and-endpoints/preloadModels";
import { preloadAccountPoolModules } from "@/features/account-pool/preloadModules";
import {
  DEFAULT_LOGS_SORTING,
  initialLogsRange,
  requestLogsQueryOptions,
} from "@/components/view_logs/log_filter_logic";
import { dashboardBackgroundTasks } from "@/lib/dashboardBackground";
import { DEFAULT_DISABLED_PAGES } from "@/lib/moduleVisibility";
import { DashboardReadinessProvider } from "@/components/DashboardReadinessContext";
import { apiClient, modelAvailableCall, userGetInfoV2 } from "@/components/networking";
import { clearTokenCookies } from "@/utils/cookieUtils";
import { accountPoolQueryKeys } from "@/features/account-pool/hooks/accountPoolQueryKeys";
import { listAccountPoolProviderFamilies } from "@/features/account-pool/api/AccountPoolApi";
import { SESSION_RESET_EVENT } from "@/lib/cacheEvents";

const PRIMARY_PAGES = ["api-keys", "models", "account-pool", "logs", "new_usage"] as const;
const PRIMARY_TASK_COUNT = PRIMARY_PAGES.length * 2;
const MAX_MODULE_DATA_CONCURRENCY = 2;
const LOG_PAGE_SIZE = 50;

const task = (id: string, run: PreloadTask["run"]): PreloadTask => ({ id, run });
const optionalTask = (id: string, run: PreloadTask["run"]): PreloadTask => ({
  id,
  run,
  critical: false,
});

const prefetchPage = async (router: ReturnType<typeof useRouter>, page: string) => {
  await router.prefetch(pageHref(page));
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
  signal: AbortSignal,
  priority: "primary" | "background" = "primary",
) => {
  if (signal.aborted) return Promise.resolve(undefined);
  const cancelIfUnobserved = () => {
    const query = queryClient.getQueryCache().find({ queryKey: options.queryKey, exact: true });
    if (query && query.getObserversCount() === 0) {
      void queryClient.cancelQueries({ queryKey: options.queryKey, exact: true });
    }
  };
  signal.addEventListener("abort", cancelIfUnobserved, { once: true });
  return queryClient
    .fetchQuery({
      ...options,
      gcTime: queryClient.getDefaultOptions().queries?.gcTime,
      meta: { ...options.meta, dashboardWarmup: priority },
    })
    .finally(() => {
      signal.removeEventListener("abort", cancelIfUnobserved);
    });
};

const runWithConcurrency = async (
  signal: AbortSignal,
  limit: number,
  tasks: readonly (() => Promise<unknown>)[],
): Promise<void> => {
  let nextIndex = 0;
  let firstError: unknown;
  let hasError = false;
  const worker = async () => {
    while (!signal.aborted && !hasError && nextIndex < tasks.length) {
      const current = tasks[nextIndex++];
      try {
        await current();
      } catch (error) {
        if (!hasError) {
          hasError = true;
          firstError = error;
        }
        return;
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, tasks.length) }, worker));
  if (hasError) throw firstError;
};

interface DashboardWarmupContext {
  queryClient: QueryClient;
  router: ReturnType<typeof useRouter>;
  accessToken: string;
  userId: string | null;
  userRole: string;
  token: string;
  isViewOnly?: boolean;
}

const isWarmupViewOnly = (userRole: string, token: string, explicit?: boolean): boolean => {
  if (explicit !== undefined) return explicit;
  if (isViewOnlyRole(userRole) || isViewOnlySessionRole(userRole)) return true;
  const rawRole = checkTokenValidity(token) ? decodeToken(token)?.user_role : undefined;
  return isViewOnlySessionRole(rawRole);
};

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

const createPrimaryTasks = (context: DashboardWarmupContext): readonly PreloadTask[] => {
  const { queryClient, router, accessToken, userId, userRole, token, isViewOnly } = context;
  const canManagePool = canManageAccountPool(userRole, isWarmupViewOnly(userRole, token, isViewOnly));
  return [
    task("virtual-keys", () => loadApiKeysPage(router)),
    optionalTask("virtual-keys-data", async (taskSignal) => {
      if (taskSignal.aborted) return;
      await runWithConcurrency(taskSignal, MAX_MODULE_DATA_CONCURRENCY, [
        () =>
          runQuery(
            queryClient,
            keyListQueryOptions(accessToken, 1, LOG_PAGE_SIZE, {
              sortBy: "created_at",
              sortOrder: "desc",
              expand: "user",
            }),
            taskSignal,
          ),
        () => runQuery(queryClient, allTeamsQueryOptions(accessToken, userId, userRole), taskSignal),
        () => runQuery(queryClient, organizationQueryOptions(accessToken, userId, userRole), taskSignal),
        ...(userId
          ? [
              () =>
                runQuery(
                  queryClient,
                  {
                    queryKey: ["dashboard-key-user", userId],
                    queryFn: () => userGetInfoV2(accessToken, userId),
                  },
                  taskSignal,
                ),
              () =>
                runQuery(
                  queryClient,
                  {
                    queryKey: ["dashboard-key-models", userId],
                    queryFn: () => modelAvailableCall(accessToken, userId, userRole),
                  },
                  taskSignal,
                ),
            ]
          : []),
      ]);
    }),
    task("models-and-endpoints", () => loadModelsPage(router)),
    optionalTask("models-and-endpoints-data", async (taskSignal) => {
      if (taskSignal.aborted) return;
      if (!userId || !rolesAllowedToViewWriteScopedPages.includes(userRole)) return;
      await runWithConcurrency(taskSignal, MAX_MODULE_DATA_CONCURRENCY, [
        () => runQuery(queryClient, modelInfoQueryOptions({ accessToken, userId, userRole }), taskSignal),
        () =>
          runQuery(
            queryClient,
            modelInfoQueryOptions({ accessToken, userId, userRole, excludeAutoRouters: true }),
            taskSignal,
          ),
        () => runQuery(queryClient, teamsQueryOptions(accessToken, userId, userRole), taskSignal),
        () => runQuery(queryClient, modelCostMapQueryOptions(), taskSignal),
        () => runQuery(queryClient, uiSettingsQueryOptions(), taskSignal),
      ]);
    }),
    task("account-pool", async (taskSignal) => {
      await loadAccountPoolPage(router);
      if (taskSignal.aborted || !canManagePool) return;
      await preloadAccountPoolModules();
    }),
    optionalTask("account-pool-data", async (taskSignal) => {
      if (taskSignal.aborted || !canManagePool) return;
      await runWithConcurrency(taskSignal, MAX_MODULE_DATA_CONCURRENCY, [
        () => runQuery(queryClient, accountPoolEnvironmentOptions(accessToken), taskSignal),
        () => runQuery(queryClient, accountPoolPolicyOptions(accessToken), taskSignal),
        () => runQuery(queryClient, accountPoolDashboardStatsOptions(accessToken), taskSignal),
        () => runQuery(queryClient, accountPoolQuotaRefreshStatusOptions(accessToken), taskSignal),
        () => runQuery(queryClient, proxyGatewayQueryOptions(accessToken), taskSignal),
        () =>
          runQuery(
            queryClient,
            {
              queryKey: accountPoolQueryKeys.providerFamilies(accessToken),
              queryFn: ({ signal: querySignal }) => listAccountPoolProviderFamilies(accessToken, querySignal),
            },
            taskSignal,
          ),
      ]);
    }),
    task("logs", async () => {
      await loadLogsPage(router);
      await preloadOperationLogModule();
    }),
    optionalTask("logs-data", async (taskSignal) => {
      if (taskSignal.aborted) return;
      await runQuery(queryClient, defaultRequestLogOptions(context, 0), taskSignal);
      if (taskSignal.aborted || !canManagePool) return;
      const filters = {};
      await runQuery(
        queryClient,
        operationLogsQueryOptions({ accessToken, filters, offset: 0, pageSize: LOG_PAGE_SIZE }),
        taskSignal,
      );
    }),
    task("usage", () => loadUsagePage(router)),
    optionalTask("usage-data", async (taskSignal) => {
      if (taskSignal.aborted) return;
      const endTime = new Date();
      const startTime = new Date(endTime.getTime() - 7 * 24 * 60 * 60 * 1000);
      const isAdmin = all_admin_roles.includes(userRole);
      const effectiveUserId = isAdmin ? null : userId;
      await runWithConcurrency(taskSignal, MAX_MODULE_DATA_CONCURRENCY, [
        () => runQuery(queryClient, teamsQueryOptions(accessToken, userId, userRole), taskSignal),
        () => runQuery(queryClient, organizationQueryOptions(accessToken, userId, userRole), taskSignal),
        () => runQuery(queryClient, currentUserQueryOptions(accessToken, userId), taskSignal),
        ...(isAdmin ? [() => runQuery(queryClient, customersQueryOptions(accessToken, userRole), taskSignal)] : []),
        ...(isAdmin ? [() => runQuery(queryClient, agentsQueryOptions(accessToken, userRole), taskSignal)] : []),
        () =>
          runQuery(
            queryClient,
            usageDailyActivityQueryOptions(accessToken, startTime, endTime, effectiveUserId),
            taskSignal,
          ),
        () => runQuery(queryClient, usageTagsQueryOptions(accessToken, startTime, endTime), taskSignal),
        ...(isAdmin
          ? [
              () =>
                runQuery(
                  queryClient,
                  usageGatewayActivityQueryOptions(accessToken, startTime, endTime, true),
                  taskSignal,
                ),
            ]
          : []),
      ]);
    }),
  ];
};

const batchesOfTwo = (tasks: readonly PreloadTask[]): PreloadTask[][] =>
  Array.from({ length: Math.ceil(tasks.length / 2) }, (_, index) => tasks.slice(index * 2, index * 2 + 2));

const createBackgroundBatches = async (
  context: DashboardWarmupContext,
  signal: AbortSignal,
  disabledPages: readonly string[],
) => {
  const { queryClient, accessToken, userRole, token, isViewOnly } = context;
  const tasks: PreloadTask[] = [...dashboardBackgroundTasks(context, signal, disabledPages)];
  tasks.push(task("other-log-modules", preloadOtherLogModules));
  if (canManageAccountPool(userRole, isWarmupViewOnly(userRole, token, isViewOnly))) {
    tasks.unshift(
      task("full-logs-module", preloadFullLogModule),
      task("log-settings-module", preloadLogSettingsModule),
      task("log-settings", (taskSignal) =>
        runQuery(
          queryClient,
          {
            queryKey: ["logs", "settings", accessToken],
            queryFn: ({ signal: querySignal }) => apiClient.get("/logs/settings", { accessToken, signal: querySignal }),
          },
          taskSignal,
          "background",
        ),
      ),
      task("full-log-storage", (taskSignal) =>
        runQuery(
          queryClient,
          {
            queryKey: ["logs", "full-log-storage", accessToken],
            queryFn: ({ signal: querySignal }) => fullLogStorage(accessToken, querySignal),
          },
          taskSignal,
          "background",
        ),
      ),
    );
    tasks.push(
      task("full-log-sessions", (taskSignal) =>
        runQuery(queryClient, fullLogSessionsQueryOptions(accessToken, {}), taskSignal, "background"),
      ),
    );
  }
  return batchesOfTwo(tasks);
};

export const runDashboardWarmup = async (
  context: DashboardWarmupContext,
  onProgress: (progress: PreloadStageProgress) => void,
  signal: AbortSignal,
  disabledPages: readonly string[] = DEFAULT_DISABLED_PAGES,
) => {
  return runPreloadStages(createPrimaryTasks(context), [], {
    onProgress,
    signal,
    loadBackground: () => createBackgroundBatches(context, signal, disabledPages),
  });
};

const progressPercent = (completed: number, total: number): number =>
  total === 0 ? 0 : Math.round((completed / total) * 100);

const progressTitle = (primaryOnly: boolean, settled: boolean, failed: number): string => {
  if (primaryOnly) return settled && failed > 0 ? "部分主要模块加载失败" : "正在准备主要模块";
  return settled && failed > 0 ? "部分预加载未完成" : "后台加载中";
};

function WarmupProgress({
  state,
  primaryOnly,
  settled,
  retry,
  relogin,
  dismiss,
}: {
  state: PreloadStageProgress;
  primaryOnly: boolean;
  settled: boolean;
  retry: () => void;
  relogin: () => void;
  dismiss: () => void;
}) {
  const progress = primaryOnly ? state.primary : state.background;
  const succeeded = progress.completed - progress.failed;
  const value = progressPercent(succeeded, progress.total);
  const title = progressTitle(primaryOnly, settled, progress.failed);
  return (
    <div
      className="fixed bottom-4 right-4 z-floating w-72 max-w-[calc(100%-2rem)] rounded-md border bg-background p-3 shadow-sm"
      role="status"
      aria-live="polite"
    >
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3 text-sm">
          <span>{title}</span>
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
        {progress.failed > 0 && (
          <div className="flex flex-wrap gap-2">
            <Button onClick={retry}>重试加载</Button>
            <Button variant="outline" onClick={relogin}>
              重新登录
            </Button>
            {settled && (
              <Button variant="ghost" onClick={dismiss}>
                关闭
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default function DashboardWarmup({
  children,
  disabledPages = DEFAULT_DISABLED_PAGES,
}: {
  children: React.ReactNode;
  disabledPages?: readonly string[];
}) {
  const { accessToken, userID, userRole, token } = useAuth();
  const { mode } = usePluginMode();
  const queryClient = useQueryClient();
  const router = useRouter();
  const [attempt, setAttempt] = useState(0);
  const [dismissedRunKey, setDismissedRunKey] = useState<string | null>(null);
  const [state, setState] = useState<PreloadStageProgress>({
    primary: { completed: 0, total: PRIMARY_TASK_COUNT, failed: 0 },
    background: { completed: 0, total: 0, failed: 0 },
    stage: "primary",
  });

  const shouldWarmup = Boolean(accessToken && mode === "ai-gateway");
  const primarySettled = state.primary.total > 0 && state.primary.completed === state.primary.total;
  const ready = !shouldWarmup || state.stage !== "primary" || primarySettled;
  const disabledPagesKey = JSON.stringify(disabledPages);
  const runKey = `${accessToken ?? ""}:${userID ?? ""}:${userRole}:${token ?? ""}:${attempt}:${disabledPagesKey}`;
  const dismissed = dismissedRunKey === runKey;

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
      },
      controller.signal,
      JSON.parse(disabledPagesKey) as string[],
    );
    return () => {
      controller.abort();
      window.removeEventListener(SESSION_RESET_EVENT, stop);
    };
  }, [accessToken, mode, queryClient, router, userID, userRole, token, attempt, disabledPagesKey]);

  const retry = () => setAttempt((value) => value + 1);
  const relogin = () => clearTokenCookies();
  const dismiss = () => setDismissedRunKey(runKey);
  const prefetchFailed = state.primary.failed + state.background.failed;
  const primaryOnly = state.stage === "primary";
  const settled = state.stage === "complete" || (primaryOnly && primarySettled);
  const failureState: PreloadStageProgress = {
    ...state,
    background: {
      completed: state.primary.completed + state.background.completed,
      total: state.primary.total + state.background.total,
      failed: prefetchFailed,
    },
  };
  return (
    <DashboardReadinessProvider value={ready}>
      {children}
      {shouldWarmup && !dismissed && (state.stage !== "complete" || prefetchFailed > 0) && (
        <WarmupProgress
          state={state.stage !== "primary" && prefetchFailed > 0 ? failureState : state}
          primaryOnly={primaryOnly}
          settled={settled}
          retry={retry}
          relogin={relogin}
          dismiss={dismiss}
        />
      )}
    </DashboardReadinessProvider>
  );
}
