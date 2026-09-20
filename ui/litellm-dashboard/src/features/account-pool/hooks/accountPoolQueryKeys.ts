/** 集中定义号池页面的 React Query 键，避免组件各自拼接造成缓存失效范围不一致。 */

const ACCOUNT_POOL_ROOT = ["account-pool"] as const;

export const accountPoolQueryKeys = {
  all: ACCOUNT_POOL_ROOT,
  environmentsRoot: [...ACCOUNT_POOL_ROOT, "environments"] as const,
  environments: (accessToken: string | null) => [...ACCOUNT_POOL_ROOT, "environments", accessToken] as const,
  providerFamilies: (accessToken: string | null) => [...ACCOUNT_POOL_ROOT, "provider-families", accessToken] as const,
  credentials: (accessToken: string | null) => [...ACCOUNT_POOL_ROOT, "credentials", accessToken] as const,
  authFileRefresh: (accessToken: string | null) => [...ACCOUNT_POOL_ROOT, "auth-file-refresh", accessToken] as const,
  onboardingSuppliers: (accessToken: string) => [...ACCOUNT_POOL_ROOT, "onboarding-suppliers", accessToken] as const,
  onboarding: (accessToken: string) => [...ACCOUNT_POOL_ROOT, "onboarding", accessToken] as const,
  onboardingTargets: (accessToken: string) => [...ACCOUNT_POOL_ROOT, "onboarding-targets", accessToken] as const,
  batches: (accessToken: string | null) => [...ACCOUNT_POOL_ROOT, "batches", accessToken] as const,
  policies: (accessToken: string | null) => [...ACCOUNT_POOL_ROOT, "policies", accessToken] as const,
  policy: (accessToken: string | null, cardId: string) =>
    [...ACCOUNT_POOL_ROOT, "policy", accessToken, cardId] as const,
  proxyProfiles: (accessToken: string | null) => [...ACCOUNT_POOL_ROOT, "proxy-profiles", accessToken] as const,
  cardPluginConfig: (accessToken: string | null, cardId: string | null, pluginId: string | null) =>
    [...ACCOUNT_POOL_ROOT, "card-plugin-config", accessToken, cardId, pluginId] as const,
  cardPlugins: (accessToken: string | null, cardId: string | null) =>
    [...ACCOUNT_POOL_ROOT, "card-plugins", accessToken, cardId] as const,
  cardPluginStore: (accessToken: string | null, cardId: string | null) =>
    [...ACCOUNT_POOL_ROOT, "card-plugin-store", accessToken, cardId] as const,
  releases: (accessToken: string | null) => [...ACCOUNT_POOL_ROOT, "releases", accessToken] as const,
  releaseCommandsRoot: (accessToken: string | null) => [...ACCOUNT_POOL_ROOT, "release-commands", accessToken] as const,
  releaseCommands: (accessToken: string | null, pairId?: string) =>
    [...ACCOUNT_POOL_ROOT, "release-commands", accessToken, pairId] as const,
  settings: (accessToken: string | null) => [...ACCOUNT_POOL_ROOT, "settings", accessToken] as const,
  nativeRouterSettings: (accessToken: string | null) =>
    [...ACCOUNT_POOL_ROOT, "native-router-settings", accessToken] as const,
  nativeGeneralSettings: (accessToken: string | null) =>
    [...ACCOUNT_POOL_ROOT, "native-general-settings", accessToken] as const,
  upstreamSync: (accessToken: string | null) => [...ACCOUNT_POOL_ROOT, "upstream-sync", accessToken] as const,
  quotaRefreshStatus: (accessToken: string | null) =>
    [...ACCOUNT_POOL_ROOT, "quota-refresh-status", accessToken] as const,
  dashboardStats: (accessToken: string | null) => [...ACCOUNT_POOL_ROOT, "dashboard-stats", accessToken] as const,
  proxyGatewaysRoot: [...ACCOUNT_POOL_ROOT, "proxy-gateways"] as const,
  proxyGateways: (accessToken: string | null) => [...ACCOUNT_POOL_ROOT, "proxy-gateways", accessToken] as const,
  proxyGatewayDelays: (accessToken: string | null) =>
    [...ACCOUNT_POOL_ROOT, "proxy-gateway-delays", accessToken] as const,
  proxyGatewayConfiguration: (accessToken: string | null) =>
    [...ACCOUNT_POOL_ROOT, "proxy-gateway-configuration", accessToken] as const,
  proxyGatewayNodes: (accessToken: string | null) =>
    [...ACCOUNT_POOL_ROOT, "proxy-gateway-nodes", accessToken] as const,
} as const;
