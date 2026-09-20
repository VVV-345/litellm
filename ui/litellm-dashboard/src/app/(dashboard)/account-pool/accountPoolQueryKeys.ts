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
  proxyGatewaysRoot: [...ACCOUNT_POOL_ROOT, "proxy-gateways"] as const,
  proxyGateways: (accessToken: string | null) => [...ACCOUNT_POOL_ROOT, "proxy-gateways", accessToken] as const,
  proxyGatewayDelays: (accessToken: string | null) =>
    [...ACCOUNT_POOL_ROOT, "proxy-gateway-delays", accessToken] as const,
  proxyGatewayConfiguration: (accessToken: string | null) =>
    [...ACCOUNT_POOL_ROOT, "proxy-gateway-configuration", accessToken] as const,
  proxyGatewayNodes: (accessToken: string | null) =>
    [...ACCOUNT_POOL_ROOT, "proxy-gateway-nodes", accessToken] as const,
} as const;
