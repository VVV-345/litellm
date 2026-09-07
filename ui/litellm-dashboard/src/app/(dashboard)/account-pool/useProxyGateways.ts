/** 本文件管理号池代理网关面板的数据查询与切换动作，组件不直接处理请求细节。 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  listAccountPoolClashNodes,
  listAccountPoolProxyGateways,
  switchAccountPoolProxyGateway,
} from "./AccountPoolApi";

export const useProxyGateways = (accessToken: string | null, enabled: boolean) => {
  const queryClient = useQueryClient();
  const queryOptions = {
    enabled: enabled && accessToken !== null,
    retry: false,
  };
  const gatewaysQuery = useQuery({
    queryKey: ["account-pool", "proxy-gateways", accessToken],
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return listAccountPoolProxyGateways(accessToken);
    },
    ...queryOptions,
  });
  const nodesQuery = useQuery({
    queryKey: ["account-pool", "proxy-gateway-nodes", accessToken],
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return listAccountPoolClashNodes(accessToken);
    },
    ...queryOptions,
  });
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["account-pool", "proxy-gateways"] });
  };
  const switchMutation = useMutation({
    mutationFn: async ({ port, nodeName }: { port: number; nodeName: string }) => {
      if (!accessToken) throw new Error("Access token required");
      return switchAccountPoolProxyGateway(accessToken, port, nodeName);
    },
    onSuccess: invalidate,
  });
  return {
    gateways: gatewaysQuery.data ?? [],
    gatewaysLoading: gatewaysQuery.isLoading || gatewaysQuery.isFetching,
    gatewaysError: gatewaysQuery.isError ? (gatewaysQuery.error.message ?? null) : null,
    refetchGateways: () => void gatewaysQuery.refetch(),
    nodes: nodesQuery.data ?? [],
    nodesLoading: nodesQuery.isLoading || nodesQuery.isFetching,
    nodesError: nodesQuery.isError ? (nodesQuery.error.message ?? null) : null,
    switchMutation,
  };
};
