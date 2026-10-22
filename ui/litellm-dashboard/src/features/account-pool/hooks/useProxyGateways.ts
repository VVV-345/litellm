/** 本文件管理号池代理网关面板的数据查询与切换动作，组件不直接处理请求细节。 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  addAccountPoolProxyGateway,
  activateAccountPoolProxyConfiguration,
  bindAccountPoolProxyGatewayISP,
  bindAccountPoolProxyGatewayFront,
  configureAccountPoolISPSubscription,
  deleteAccountPoolISPProxy,
  deleteAccountPoolProxyGateway,
  deleteAccountPoolProxyConfiguration,
  getAccountPoolProxyGatewayConfiguration,
  getAccountPoolISPSubscription,
  importAccountPoolISPProxies,
  listAccountPoolClashNodes,
  listAccountPoolISPProxies,
  listAccountPoolProxyConfigurations,
  listAccountPoolProxyGateways,
  measureAccountPoolProxyGatewayDelays,
  probeAccountPoolISP,
  probeAccountPoolProxyGatewayISP,
  refreshAccountPoolISPSubscription,
  saveAccountPoolProxyConfiguration,
  switchAccountPoolProxyGateway,
  updateAccountPoolISPProxy,
  updateAccountPoolProxyGatewayConfiguration,
} from "../api/AccountPoolApi";
import { accountPoolQueryKeys } from "./accountPoolQueryKeys";
import type {
  AccountPoolISPProxyUpdateRequest,
  AccountPoolISPProxy,
  AccountPoolISPSubscription,
  AccountPoolGatewayISPProbe,
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

export const useProxyGatewayDelays = (accessToken: string | null, enabled: boolean) =>
  useQuery({
    queryKey: accountPoolQueryKeys.proxyGatewayDelays(accessToken),
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return measureAccountPoolProxyGatewayDelays(accessToken);
    },
    enabled: enabled && accessToken !== null,
    retry: false,
    staleTime: 60_000,
    refetchOnMount: true,
    refetchOnWindowFocus: false,
    refetchInterval: (query) =>
      query.state.status === "error" ? 60_000 : Math.max(1000, 60_000 - (Date.now() - query.state.dataUpdatedAt)),
  });

export const useProxyISPQuery = (accessToken: string | null, enabled: boolean) =>
  useQuery({
    queryKey: accountPoolQueryKeys.proxyIsps(accessToken),
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return listAccountPoolISPProxies(accessToken);
    },
    enabled: enabled && accessToken !== null,
    retry: false,
    staleTime: 15_000,
    refetchInterval: 30_000,
  });

export const useProxyGatewayISPProbe = (accessToken: string | null, port: number) =>
  useQuery<AccountPoolGatewayISPProbe>({
    queryKey: accountPoolQueryKeys.proxyGatewayISPProbe(accessToken, port),
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return probeAccountPoolProxyGatewayISP(accessToken, port);
    },
    enabled: false,
    retry: false,
    staleTime: 0,
  });

export const useProxyGateways = (accessToken: string | null, enabled: boolean) => {
  const queryClient = useQueryClient();
  const gatewaysQuery = useProxyGatewayQuery(accessToken, enabled);
  const delaysKey = accountPoolQueryKeys.proxyGatewayDelays(accessToken);
  const delaysQuery = useProxyGatewayDelays(accessToken, enabled);
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
  const ispsQuery = useProxyISPQuery(accessToken, enabled);
  const subscriptionKey = accountPoolQueryKeys.proxyISPSubscription(accessToken);
  const ispSubscriptionQuery = useQuery<AccountPoolISPSubscription>({
    queryKey: subscriptionKey,
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return getAccountPoolISPSubscription(accessToken);
    },
    enabled: enabled && accessToken !== null,
    retry: false,
    staleTime: 15_000,
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
            current?.map((profile) =>
              profile.id === profileId &&
              (!profile.last_probe || new Date(result.checked_at) >= new Date(profile.last_probe.checked_at))
                ? { ...profile, last_probe: result }
                : profile,
            ),
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
    void queryClient.invalidateQueries({ queryKey: subscriptionKey });
    void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.proxyGatewaysRoot });
    void queryClient.resetQueries({ queryKey: delaysKey });
    void queryClient.resetQueries({ queryKey: accountPoolQueryKeys.proxyGatewayISPProbesRoot(accessToken) });
  };
  const switchMutation = useMutation({
    mutationFn: async ({ port, nodeName }: { port: number; nodeName: string }) => {
      if (!accessToken) throw new Error("Access token required");
      return switchAccountPoolProxyGateway(accessToken, port, nodeName);
    },
    onSuccess: (gateway) => {
      queryClient.setQueryData<AccountPoolProxyGateway[]>(accountPoolQueryKeys.proxyGateways(accessToken), (current) =>
        current?.map((item) => (item.port === gateway.port ? gateway : item)),
      );
      void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.proxyIsps(accessToken) });
      void queryClient.resetQueries({ queryKey: accountPoolQueryKeys.proxyGatewayISPProbesRoot(accessToken) });
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
      void queryClient.resetQueries({ queryKey: accountPoolQueryKeys.proxyGatewayISPProbesRoot(accessToken) });
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
  const configureISPSubscriptionMutation = useMutation({
    mutationFn: async (url: string) => {
      if (!accessToken) throw new Error("Access token required");
      return configureAccountPoolISPSubscription(accessToken, url);
    },
    onSuccess: invalidate,
  });
  const refreshISPSubscriptionMutation = useMutation({
    mutationFn: async () => {
      if (!accessToken) throw new Error("Access token required");
      return refreshAccountPoolISPSubscription(accessToken);
    },
    onSuccess: invalidate,
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
  const bindFrontMutation = useMutation({
    mutationFn: async ({ port, nodeName }: { port: number; nodeName: string | null }) => {
      if (!accessToken) throw new Error("Access token required");
      return bindAccountPoolProxyGatewayFront(accessToken, port, nodeName);
    },
    onSuccess: (gateway) => {
      queryClient.setQueryData<AccountPoolProxyGateway[]>(accountPoolQueryKeys.proxyGateways(accessToken), (current) =>
        current?.map((item) => (item.port === gateway.port ? gateway : item)),
      );
      invalidate();
    },
  });
  return {
    gateways: gatewaysQuery.data ?? [],
    gatewaysLoading: gatewaysQuery.isLoading || gatewaysQuery.isFetching,
    gatewaysError: gatewaysQuery.isError ? gatewaysQuery.error.message ?? null : null,
    configuration: configurationQuery.data ?? null,
    configurations: configurationsQuery.data ?? [],
    isps: ispsQuery.data ?? [],
    ispSubscription: ispSubscriptionQuery.data ?? null,
    ispSubscriptionLoading: ispSubscriptionQuery.isLoading,
    ispSubscriptionError: ispSubscriptionQuery.isError,
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
      void ispSubscriptionQuery.refetch();
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
    configureISPSubscriptionMutation,
    refreshISPSubscriptionMutation,
    ispUpdateMutation,
    ispDeleteMutation,
    bindISPMutation,
    bindFrontMutation,
  };
};
