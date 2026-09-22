import type { QueryClient } from "@tanstack/react-query";
import * as api from "@/components/networking";
import { all_admin_roles, spendScopeUserId } from "@/utils/roles";
import { toolPoliciesListOptions } from "@/components/ToolPolicies/toolPoliciesQueries";
import { $api } from "@/lib/http/api";
import { teamListCall } from "@/app/(dashboard)/hooks/teams/useTeams";
import { teamListScopeUserId } from "@/utils/roles";
import { hasCapability, type Capability } from "@/utils/capabilities";
import type { PreloadTask } from "./progressivePreload";

const routes = {
  "api-reference": () => import("@/app/(dashboard)/api-reference/page"),
  playground: () => import("@/app/(dashboard)/playground/page"),
  projects: () => import("@/app/(dashboard)/projects/page"),
  "access-groups": () => import("@/app/(dashboard)/access-groups/page"),
  budgets: () => import("@/app/(dashboard)/budgets/page"),
  workflows: () => import("@/app/(dashboard)/workflows/page"),
  "guardrails-monitor": () => import("@/app/(dashboard)/guardrails-monitor/page"),
  "mcp-servers": () => import("@/app/(dashboard)/mcp-servers/page"),
  "search-tools": () => import("@/app/(dashboard)/search-tools/page"),
  "tag-management": () => import("@/app/(dashboard)/tag-management/page"),
  "vector-stores": () => import("@/app/(dashboard)/vector-stores/page"),
  memory: () => import("@/app/(dashboard)/memory/page"),
  policies: () => import("@/app/(dashboard)/policies/page"),
  guardrails: () => import("@/app/(dashboard)/guardrails/page"),
  prompts: () => import("@/app/(dashboard)/prompts/page"),
  "tool-policies": () => import("@/app/(dashboard)/tool-policies/page"),
  skills: () => import("@/app/(dashboard)/skills/page"),
  caching: () => import("@/app/(dashboard)/caching/page"),
  "cost-tracking": () => import("@/app/(dashboard)/cost-tracking/page"),
  "transform-request": () => import("@/app/(dashboard)/transform-request/page"),
  "ui-theme": () => import("@/app/(dashboard)/ui-theme/page"),
  "admin-panel": () => import("@/app/(dashboard)/admin-panel/page"),
  "logging-and-alerts": () => import("@/app/(dashboard)/logging-and-alerts/page"),
  "model-hub-table": () => import("@/app/(dashboard)/model-hub-table/page"),
  "old-usage": () => import("@/app/(dashboard)/old-usage/page"),
  "cost-optimization": () => import("@/app/(dashboard)/cost-optimization/page"),
  agents: () => import("@/app/(dashboard)/agents/page"),
  "router-settings": () => import("@/app/(dashboard)/router-settings/page"),
  users: () => import("@/app/(dashboard)/users/page"),
  teams: () => import("@/app/(dashboard)/teams/page"),
  organizations: () => import("@/app/(dashboard)/organizations/page"),
};

export const dashboardBackgroundTasks = (
  {
    queryClient,
    accessToken,
    userId,
    userRole,
  }: {
    queryClient: QueryClient;
    accessToken: string;
    userId: string | null;
    userRole: string;
  },
  signal: AbortSignal,
): PreloadTask[] => {
  const get = (path: string) => api.apiClient.get(path, { accessToken });
  const end = new Date();
  const start = new Date(end.getTime() - 7 * 86400_000);
  const initialData: Partial<Record<keyof typeof routes, () => Promise<unknown>>> = {
    "api-reference": () => api.getProxyUISettings(accessToken),
    playground: () => api.getProxyUISettings(accessToken),
    projects: () =>
      queryClient.fetchQuery({ queryKey: ["projects", "list", { params: {} }], queryFn: () => get("/project/list") }),
    "access-groups": () =>
      queryClient.fetchQuery({
        queryKey: ["accessGroups", "list", { params: {} }],
        queryFn: () => get("/v1/access_group"),
      }),
    budgets: () =>
      queryClient.fetchQuery({
        queryKey: ["budgets", "list", { page: 1, page_size: 50, sort: "-created_at" }],
        queryFn: () => get("/management/v1/budgets?page=1&page_size=50&sort=-created_at"),
      }),
    workflows: () => get("/v1/workflows/runs?limit=100"),
    "guardrails-monitor": () => api.getGuardrailsUsageOverview(accessToken, api.formatDate(start), api.formatDate(end)),
    "mcp-servers": () =>
      queryClient.fetchQuery({ queryKey: ["mcpServers", "list", {}], queryFn: () => api.fetchMCPServers(accessToken) }),
    "search-tools": () =>
      Promise.all([
        queryClient.fetchQuery({
          queryKey: ["searchTools"],
          queryFn: async () => (await api.fetchSearchTools(accessToken)).search_tools || [],
        }),
        queryClient.fetchQuery({
          queryKey: ["searchProviders"],
          queryFn: () => api.fetchAvailableSearchProviders(accessToken),
        }),
      ]),
    "tag-management": () => api.tagListCall(accessToken),
    "vector-stores": () => Promise.all([api.vectorStoreListCall(accessToken), api.credentialListCall(accessToken)]),
    memory: () =>
      queryClient.fetchQuery({
        queryKey: ["memoryList", "", 0, 50],
        queryFn: () => api.fetchMemoryList(accessToken, { page: 1, pageSize: 50 }),
      }),
    policies: () =>
      Promise.all([
        api.getPoliciesList(accessToken),
        api.getPolicyAttachmentsList(accessToken),
        api.getGuardrailsList(accessToken),
      ]),
    guardrails: () => api.getGuardrailsList(accessToken),
    prompts: () => api.getPromptsList(accessToken),
    "tool-policies": () => queryClient.fetchQuery(toolPoliciesListOptions(accessToken)),
    skills: () => api.getClaudeCodePluginsList(accessToken),
    caching: () =>
      queryClient.fetchQuery(
        $api.queryOptions("get", "/global/activity/cache_hits", {
          params: {
            query: {
              start_date: start.toISOString().split("T")[0],
              end_date: end.toISOString().split("T")[0],
              key_aliases: [],
              models: [],
            },
          },
        }),
      ),
    "cost-tracking": () => Promise.all([get("/config/cost_discount_config"), get("/config/cost_margin_config")]),
    "ui-theme": () => get("/get/ui_theme_settings"),
    "admin-panel": () => Promise.all([api.getSSOSettings(accessToken), api.getUISettings(accessToken)]),
    "logging-and-alerts": () =>
      Promise.all([api.getCallbacksCall(accessToken, userId ?? "", userRole), api.getCallbackConfigsCall(accessToken)]),
    "model-hub-table": () => api.modelHubCall(accessToken),
    "old-usage": async () => {
      const settings = await api.getProxyUISettings(accessToken);
      if (settings?.DISABLE_EXPENSIVE_DB_QUERIES) return;
      const monthStart = api.formatDate(new Date(end.getFullYear(), end.getMonth(), 1));
      const monthEnd = api.formatDate(new Date(end.getFullYear(), end.getMonth() + 1, 0));
      for (const read of [
        () => api.adminSpendLogsCall(accessToken),
        () => api.adminspendByProvider(accessToken, monthStart, monthEnd),
        () => api.adminTopKeysCall(accessToken),
        () => api.adminTopModelsCall(accessToken),
        () => api.adminGlobalActivity(accessToken, monthStart, monthEnd),
        () => api.adminGlobalActivityPerModel(accessToken, monthStart, monthEnd),
        () => api.teamSpendLogsCall(accessToken),
        () => api.allTagNamesCall(accessToken),
        () => api.adminTopEndUsersCall(accessToken, null, undefined, undefined),
      ]) {
        if (signal.aborted) return;
        await read();
      }
    },
    "cost-optimization": () =>
      api.userDailyActivityAggregatedCall(
        accessToken,
        new Date(end.getTime() - 30 * 86400_000),
        end,
        spendScopeUserId(userRole, userId),
        true,
      ),
    agents: () => api.getAgentsList(accessToken),
    "router-settings": () =>
      Promise.all([api.getRouterSettingsCall(accessToken), api.getGeneralSettingsCall(accessToken)]),
    users: () => api.userListCall(accessToken, null, 1, 50, null, null, null, null, "created_at", "desc"),
    teams: () =>
      teamListCall(accessToken, 1, 50, {
        userID: teamListScopeUserId(userRole, userId),
        sortBy: "created_at",
        sortOrder: "desc",
      }),
    organizations: () => api.organizationListCall(accessToken, null, null),
  };
  const adminPages = new Set([
    "access-groups",
    "budgets",
    "skills",
    "caching",
    "cost-tracking",
    "ui-theme",
    "admin-panel",
    "logging-and-alerts",
    "router-settings",
    "users",
    "organizations",
    "agents",
  ]);
  const capabilities: Partial<Record<keyof typeof routes, Capability>> = {
    workflows: "viewWorkflowRuns",
    "guardrails-monitor": "viewGuardrailUsage",
    memory: "viewMemory",
    "tool-policies": "viewToolPolicies",
    policies: "viewPolicies",
    prompts: "viewPrompts",
    "cost-optimization": "viewProxyWideCostData",
    "old-usage": "viewGlobalSpend",
  };
  return Object.entries(routes).map(([page, load]) => ({
    id: `page:${page}`,
    run: async () => {
      await load();
      if (signal.aborted) return;
      if (adminPages.has(page) && !all_admin_roles.includes(userRole)) return;
      const capability = capabilities[page as keyof typeof routes];
      if (capability && !hasCapability(userRole, capability)) return;
      const loadData = initialData[page as keyof typeof routes];
      if (loadData)
        await queryClient.fetchQuery({
          queryKey: ["dashboard-page-data", page],
          queryFn: async () => {
            await loadData();
            return true;
          },
          meta: { dashboardWarmup: "background" },
        });
    },
  }));
};
