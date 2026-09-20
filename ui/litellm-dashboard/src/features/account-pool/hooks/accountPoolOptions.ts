/** 复用卡片策略与代理下拉列表的查询定义，由调用页面决定加载时机和刷新策略。 */
import { queryOptions } from "@tanstack/react-query";
import { listAccountPoolProxyProfiles } from "../api/AccountPoolApi";
import { listAccountPolicies } from "../api/AccountPoolManagementApi";
import { accountPoolQueryKeys } from "./accountPoolQueryKeys";

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
