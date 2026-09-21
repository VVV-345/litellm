/** 本文件集中管理号池环境查询状态与刷新策略。 */

import { useQuery } from "@tanstack/react-query";

import { listAccountPoolEnvironments } from "../api/AccountPoolApi";
import type { AccountPoolEnvironment } from "../utils/AccountPoolTypes";
import { accountPoolQueryKeys } from "./accountPoolQueryKeys";

export const ACCOUNT_POOL_ENVIRONMENTS_QUERY_KEY = accountPoolQueryKeys.environmentsRoot;

export const useAccountPoolQuery = (accessToken: string | null, enabled: boolean, poll = true) =>
  useQuery<AccountPoolEnvironment[]>({
    queryKey: accountPoolQueryKeys.environments(accessToken),
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return listAccountPoolEnvironments(accessToken);
    },
    enabled: enabled && accessToken !== null,
    retry: false,
    staleTime: 10_000,
    refetchOnWindowFocus: true,
    refetchInterval: (query) => {
      if (!poll) return false;
      return query.state.data?.some((environment) => environment.status === "awaiting_authorization") ? 5000 : 15000;
    },
  });
