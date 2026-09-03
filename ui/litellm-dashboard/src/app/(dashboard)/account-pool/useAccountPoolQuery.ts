/** 本文件集中管理号池环境查询状态与刷新策略。 */

import { useQuery } from "@tanstack/react-query";

import { listAccountPoolEnvironments } from "./AccountPoolApi";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";

export const ACCOUNT_POOL_ENVIRONMENTS_QUERY_KEY = ["account-pool", "environments"] as const;

export const useAccountPoolQuery = (accessToken: string | null, enabled: boolean) =>
  useQuery<AccountPoolEnvironment[]>({
    queryKey: ACCOUNT_POOL_ENVIRONMENTS_QUERY_KEY,
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return listAccountPoolEnvironments(accessToken);
    },
    enabled: enabled && accessToken !== null,
    refetchInterval: (query) =>
      query.state.data?.some((environment) => environment.status === "awaiting_authorization") ? 5000 : 15000,
  });
