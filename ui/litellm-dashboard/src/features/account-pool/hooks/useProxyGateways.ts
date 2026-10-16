/** 本文件管理号池代理网关面板的数据查询与切换动作，组件不直接处理请求细节。 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  addAccountPoolProxyGateway,
  activateAccountPoolProxyConfiguration,
  bindAccountPoolProxyGatewayISP,
  deleteAccountPoolISPProxy,
  deleteAccountPoolProxyGateway,
  deleteAccountPoolProxyConfiguration,
  getAccountPoolProxyGatewayConfiguration,
  importAccountPoolISPProxies,
  listAccountPoolClashNodes,
  listAccountPoolISPProxies,
  listAccountPoolProxyConfigurations,
  listAccountPoolProxyGateways,
  measureAccountPoolProxyGatewayDelays,
  probeAccountPoolISP,
  saveAccountPoolProxyConfiguration,
  switchAccountPoolProxyGateway,
  updateAccountPoolISPProxy,
  updateAccountPoolProxyGatewayConfiguration,
} from "../api/AccountPoolApi";
import { accountPoolQueryKeys } from "./accountPoolQueryKeys";
import type {
  AccountPoolISPProxyUpdateRequest,
  AccountPoolISPProxy,
  AccountPoolProxyConfigurationProfileRequest,
  AccountPoolProxyGateway,
} from "../utils/AccountPoolTypes";

export const proxyGatewayQueryOptions = (accessToken: string | null) => ({
  queryKey: accountPoolQueryKeys.proxyGateways(accessToken),
  queryFn: () => {
    if (!accessToken) throw new Error("Access token required");
    return listAccountPoolProxyGateways(accessToken);
  },
  retry: false,
  staleTime: 15_000,
  refetchOnWindowFocus: false,
  refetchInterval: 30_000,
});

export const useProxyGatewayQuery = (accessToken: string | null, enabled: boolean) =>
  useQuery({
    ...proxyGatewayQueryOptions(accessToken),
    enabled: enabled && accessToken !== null,
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
  const configurationsQuery = useQuery({
    queryKey: accountPoolQueryKeys.proxyConfigurations(accessToken),
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return listAccountPoolProxyConfigurations(accessToken);
    },
    enabled: enabled && accessToken !== null,
    retry: false,
    staleTime: 60_000,
  });
  const ispsKey = accountPoolQueryKeys.proxyIsps(accessToken);
  const ispsQuery = useQuery({
    queryKey: ispsKey,
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return listAccountPoolISPProxies(accessToken);
    },
    enabled: enabled && accessToken !== null,
    retry: false,
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
  const ispProbeMutation = useMutation({
    mutationFn: async (profileIds: string[]) => {
      if (!enabled || !accessToken) throw new Error("Access token required");
      let firstError: unknown = null;
      for (const profileId of profileIds) {
        try {
          const result = await probeAccountPoolISP(accessToken, profileId);
          await queryClient.cancelQueries({ queryKey: ispsKey, exact: true });
          queryClient.setQueryData<AccountPoolISPProxy[]>(ispsKey, (current) =>
            current?.map((profile) => profile.id === profileId &&
              (!profile.last_probe || new Date(result.checked_at) >= new Date(profile.last_probe.checked_at))
              ? { ...profile, last_probe: result }
              : profile),
          );
        } catch (error) {
          firstError ??= error;
        }
      }
      if (firstError !== null) throw firstError;
    },
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
    void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.proxyIsps(accessToken) });
    void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.proxyGatewayNodes(accessToken) });
    void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.proxyGatewaysRoot });
    void queryClient.resetQueries({ queryKey: delaysKey });
  };
  const switchMutation = useMutation({
    mutationFn: async ({ port, nodeName }: { port: number; nodeName: string }) => {
      if (!accessToken) throw new Error("Access token required");
      return switchAccountPoolProxyGateway(accessToken, port, nodeName);
    },
    onSuccess: (gateway) => {
      queryClient.setQueryData<AccountPoolProxyGateway[]>(
        accountPoolQueryKeys.proxyGateways(accessToken),
        (current) => current?.map((item) => (item.port === gateway.port ? gateway : item)),
      );
      void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.proxyIsps(accessToken) });
    },
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
  const configurationMutation = useMutation({
    mutationFn: async ({ content, url, name }: { content?: string; url?: string; name?: string }) => {
      if (!accessToken) throw new Error("Access token required");
      return updateAccountPoolProxyGatewayConfiguration(accessToken, { content, url, name });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.proxyConfigurations(accessToken) });
      void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.proxyGatewayNodes(accessToken) });
      void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.proxyGatewayConfiguration(accessToken) });
      void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.proxyGatewaysRoot });
    },
  });
  const configurationSaveMutation = useMutation({
    mutationFn: async (request: AccountPoolProxyConfigurationProfileRequest) => {
      if (!accessToken) throw new Error("Access token required");
      return saveAccountPoolProxyConfiguration(accessToken, request);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.proxyConfigurations(accessToken) });
    },
  });
  const configurationActivateMutation = useMutation({
    mutationFn: async (profileId: string) => {
      if (!accessToken) throw new Error("Access token required");
      return activateAccountPoolProxyConfiguration(accessToken, profileId);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.proxyConfigurations(accessToken) });
      invalidate();
    },
  });
  const configurationDeleteMutation = useMutation({
    mutationFn: async (profileId: string) => {
      if (!accessToken) throw new Error("Access token required");
      return deleteAccountPoolProxyConfiguration(accessToken, profileId);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.proxyConfigurations(accessToken) });
    },
  });
  const ispImportMutation = useMutation({
    mutationFn: async (content: string) => {
      if (!accessToken) throw new Error("Access token required");
      return importAccountPoolISPProxies(accessToken, content);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.proxyIsps(accessToken) });
    },
  });
  const ispUpdateMutation = useMutation({
    mutationFn: async ({ profileId, request }: { profileId: string; request: AccountPoolISPProxyUpdateRequest }) => {
      if (!accessToken) throw new Error("Access token required");
      return updateAccountPoolISPProxy(accessToken, profileId, request);
    },
    onSuccess: invalidate,
  });
  const ispDeleteMutation = useMutation({
    mutationFn: async (profileId: string) => {
      if (!accessToken) throw new Error("Access token required");
      return deleteAccountPoolISPProxy(accessToken, profileId);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.proxyIsps(accessToken) });
    },
  });
  const bindISPMutation = useMutation({
    mutationFn: async ({ port, profileId }: { port: number; profileId: string }) => {
      if (!accessToken) throw new Error("Access token required");
      return bindAccountPoolProxyGatewayISP(accessToken, port, profileId);
    },
    onSuccess: invalidate,
  });
  return {
    gateways: gatewaysQuery.data ?? [],
    gatewaysLoading: gatewaysQuery.isLoading || gatewaysQuery.isFetching,
    gatewaysError: gatewaysQuery.isError ? gatewaysQuery.error.message ?? null : null,
    configuration: configurationQuery.data ?? null,
    configurations: configurationsQuery.data ?? [],
    isps: ispsQuery.data ?? [],
    ispProbeMutation,
    ispsLoading: ispsQuery.isLoading,
    ispsError: ispsQuery.isError,
    delays: delaysQuery.data ?? [],
    delaysLoading: delaysQuery.isFetching,
    delaysError: delaysQuery.isError,
    refetchGateways: () => {
      void gatewaysQuery.refetch();
      void delaysQuery.refetch();
      void nodesQuery.refetch();
      void configurationQuery.refetch();
      void configurationsQuery.refetch();
      void ispsQuery.refetch();
    },
    nodes: nodesQuery.data ?? [],
    nodesLoading: nodesQuery.isLoading || nodesQuery.isFetching,
    nodesError: nodesQuery.isError ? nodesQuery.error.message ?? null : null,
    switchMutation,
    addMutation,
    deleteMutation,
    configurationMutation,
    configurationSaveMutation,
    configurationActivateMutation,
    configurationDeleteMutation,
    ispImportMutation,
    ispUpdateMutation,
    ispDeleteMutation,
    bindISPMutation,
  };
};
