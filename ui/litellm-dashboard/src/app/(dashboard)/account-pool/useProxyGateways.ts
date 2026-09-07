/** 本文件管理号池代理网关面板的数据查询与切换动作，组件不直接处理请求细节。 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getAccountPoolProxyGatewayConfiguration,
  listAccountPoolClashNodes,
  listAccountPoolProxyGateways,
  switchAccountPoolProxyGateway,
} from "./AccountPoolApi";

export const useProxyGatewayQuery = (accessToken: string | null, enabled: boolean) =>
  useQuery({
    queryKey: ["account-pool", "proxy-gateways", accessToken],
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return listAccountPoolProxyGateways(accessToken);
    },
    enabled: enabled && accessToken !== null,
    retry: false,
    staleTime: 15_000,
    refetchInterval: 30_000,
  });

export const useProxyGateways = (accessToken: string | null, enabled: boolean) => {
  const queryClient = useQueryClient();
  const gatewaysQuery = useProxyGatewayQuery(accessToken, enabled);
  const configurationQuery = useQuery({
    queryKey: ["account-pool", "proxy-gateway-configuration", accessToken],
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return getAccountPoolProxyGatewayConfiguration(accessToken);
    },
    enabled: enabled && accessToken !== null,
    retry: false,
    staleTime: 60_000,
  });
  const nodesQuery = useQuery({
    queryKey: ["account-pool", "proxy-gateway-nodes", accessToken],
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return listAccountPoolClashNodes(accessToken);
    },
    enabled: enabled && accessToken !== null,
    retry: false,
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
    gatewaysError: gatewaysQuery.isError ? gatewaysQuery.error.message ?? null : null,
    configuration: configurationQuery.data ?? null,
    refetchGateways: () => void gatewaysQuery.refetch(),
    nodes: nodesQuery.data ?? [],
    nodesLoading: nodesQuery.isLoading || nodesQuery.isFetching,
    nodesError: nodesQuery.isError ? nodesQuery.error.message ?? null : null,
    switchMutation,
  };
};
