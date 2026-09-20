/** 本文件管理号池代理网关面板的数据查询与切换动作，组件不直接处理请求细节。 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  addAccountPoolProxyGateway,
  deleteAccountPoolProxyGateway,
  getAccountPoolProxyGatewayConfiguration,
  listAccountPoolClashNodes,
  listAccountPoolProxyGateways,
  measureAccountPoolProxyGatewayDelays,
  switchAccountPoolProxyGateway,
} from "../api/AccountPoolApi";
import { accountPoolQueryKeys } from "./accountPoolQueryKeys";

export const useProxyGatewayQuery = (accessToken: string | null, enabled: boolean) =>
  useQuery({
    queryKey: accountPoolQueryKeys.proxyGateways(accessToken),
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return listAccountPoolProxyGateways(accessToken);
    },
    enabled: enabled && accessToken !== null,
    retry: false,
    staleTime: 15_000,
    refetchOnWindowFocus: false,
    refetchInterval: 30_000,
  });

export const useProxyGateways = (accessToken: string | null, enabled: boolean) => {
  const queryClient = useQueryClient();
  const gatewaysQuery = useProxyGatewayQuery(accessToken, enabled);
  const delaysKey = accountPoolQueryKeys.proxyGatewayDelays(accessToken);
  const delaysQuery = useQuery({
    queryKey: delaysKey,
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return measureAccountPoolProxyGatewayDelays(accessToken);
    },
    enabled: enabled && accessToken !== null,
    retry: false,
    staleTime: Infinity,
    refetchOnMount: "always",
    refetchOnWindowFocus: false,
  });
  const configurationQuery = useQuery({
    queryKey: accountPoolQueryKeys.proxyGatewayConfiguration(accessToken),
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return getAccountPoolProxyGatewayConfiguration(accessToken);
    },
    enabled: enabled && accessToken !== null,
    retry: false,
    staleTime: 60_000,
  });
  const nodesQuery = useQuery({
    queryKey: accountPoolQueryKeys.proxyGatewayNodes(accessToken),
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return listAccountPoolClashNodes(accessToken);
    },
    enabled: enabled && accessToken !== null,
    retry: false,
  });
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.proxyGatewaysRoot });
    void queryClient.resetQueries({ queryKey: delaysKey });
  };
  const switchMutation = useMutation({
    mutationFn: async ({ port, nodeName }: { port: number; nodeName: string }) => {
      if (!accessToken) throw new Error("Access token required");
      return switchAccountPoolProxyGateway(accessToken, port, nodeName);
    },
    onSuccess: invalidate,
  });
  const addMutation = useMutation({
    mutationFn: async () => {
      if (!accessToken) throw new Error("Access token required");
      return addAccountPoolProxyGateway(accessToken);
    },
    onSuccess: invalidate,
  });
  const deleteMutation = useMutation({
    mutationFn: async (port: number) => {
      if (!accessToken) throw new Error("Access token required");
      return deleteAccountPoolProxyGateway(accessToken, port);
    },
    onSuccess: invalidate,
  });
  return {
    gateways: gatewaysQuery.data ?? [],
    gatewaysLoading: gatewaysQuery.isLoading || gatewaysQuery.isFetching,
    gatewaysError: gatewaysQuery.isError ? gatewaysQuery.error.message ?? null : null,
    configuration: configurationQuery.data ?? null,
    delays: delaysQuery.data ?? [],
    delaysLoading: delaysQuery.isFetching,
    delaysError: delaysQuery.isError,
    refetchGateways: () => {
      void gatewaysQuery.refetch();
      void delaysQuery.refetch();
      void nodesQuery.refetch();
      void configurationQuery.refetch();
    },
    nodes: nodesQuery.data ?? [],
    nodesLoading: nodesQuery.isLoading || nodesQuery.isFetching,
    nodesError: nodesQuery.isError ? nodesQuery.error.message ?? null : null,
    switchMutation,
    addMutation,
    deleteMutation,
  };
};
