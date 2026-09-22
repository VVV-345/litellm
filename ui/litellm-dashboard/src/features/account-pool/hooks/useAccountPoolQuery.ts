/** 本文件集中管理号池环境查询状态与刷新策略。 */

import { useQuery } from "@tanstack/react-query";

import { accountPoolEnvironmentOptions } from "./accountPoolOptions";
import { accountPoolQueryKeys } from "./accountPoolQueryKeys";

export const ACCOUNT_POOL_ENVIRONMENTS_QUERY_KEY = accountPoolQueryKeys.environmentsRoot;

export const useAccountPoolQuery = (accessToken: string | null, enabled: boolean, poll = true) =>
  useQuery({
    ...accountPoolEnvironmentOptions(accessToken),
    enabled: enabled && accessToken !== null,
    refetchInterval: (query) => {
      if (!poll) return false;
      return query.state.data?.some((environment) => environment.status === "awaiting_authorization") ? 5000 : 15000;
    },
  });
