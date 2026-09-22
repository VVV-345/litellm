/** 复用卡片策略与代理下拉列表的查询定义，由调用页面决定加载时机和刷新策略。 */
import { queryOptions } from "@tanstack/react-query";
import { listAccountPoolEnvironments, listAccountPoolProxyProfiles } from "../api/AccountPoolApi";
import {
  getAccountPoolDashboardStats,
  getAccountPoolQuotaRefreshStatus,
  listAccountPolicies,
} from "../api/AccountPoolManagementApi";
import type { AccountPoolEnvironment } from "../utils/AccountPoolTypes";
import { accountPoolQueryKeys } from "./accountPoolQueryKeys";

export const accountPoolEnvironmentOptions = (accessToken: string | null) =>
  queryOptions<AccountPoolEnvironment[]>({
    queryKey: accountPoolQueryKeys.environments(accessToken),
    queryFn: (): Promise<AccountPoolEnvironment[]> => {
      if (!accessToken) throw new Error("Access token required");
      return listAccountPoolEnvironments(accessToken);
    },
    retry: false,
    staleTime: 10_000,
    refetchOnWindowFocus: true,
  });

export const accountPoolDashboardStatsOptions = (accessToken: string | null) =>
  queryOptions({
    queryKey: accountPoolQueryKeys.dashboardStats(accessToken),
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return getAccountPoolDashboardStats(accessToken);
    },
    retry: false,
    staleTime: 15_000,
    refetchOnWindowFocus: true,
  });

export const accountPoolQuotaRefreshStatusOptions = (accessToken: string | null) =>
  queryOptions({
    queryKey: accountPoolQueryKeys.quotaRefreshStatus(accessToken),
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return getAccountPoolQuotaRefreshStatus(accessToken);
    },
    retry: false,
    staleTime: 15_000,
    refetchOnWindowFocus: true,
  });

export const accountPoolPolicyOptions = (accessToken: string | null) =>
  queryOptions({
    queryKey: accountPoolQueryKeys.policies(accessToken),
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return listAccountPolicies(accessToken);
    },
    retry: false,
  });

export const accountPoolProxyProfileOptions = (accessToken: string | null) =>
  queryOptions({
    queryKey: accountPoolQueryKeys.proxyProfiles(accessToken),
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return listAccountPoolProxyProfiles(accessToken);
    },
    retry: false,
  });
