/** 为创建和授权页面复用代理目录、默认出口与完整卡片统计。 */
import { useQuery } from "@tanstack/react-query";
import { getAccountPoolSettings } from "../api/AccountPoolManagementApi";
import { proxyOptionLabel } from "../utils/AccountPoolProxyOptions";
import { accountPoolEnvironmentOptions, accountPoolProxyProfileOptions } from "./accountPoolOptions";
import { accountPoolQueryKeys } from "./accountPoolQueryKeys";
import { proxyGatewayQueryOptions } from "./useProxyGateways";

export const useAccountPoolProxyOptions = (accessToken: string | null, enabled: boolean) => {
  const active = enabled && accessToken !== null;
  const profilesQuery = useQuery({ ...accountPoolProxyProfileOptions(accessToken), enabled: active });
  const gatewaysQuery = useQuery({ ...proxyGatewayQueryOptions(accessToken), enabled: active });
  const cardsQuery = useQuery({ ...accountPoolEnvironmentOptions(accessToken), enabled: active, refetchInterval: 30_000 });
  const settingsQuery = useQuery({
    queryKey: accountPoolQueryKeys.settings(accessToken),
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return getAccountPoolSettings(accessToken);
    },
    enabled: active,
    retry: false,
  });
  const profiles = profilesQuery.data ?? [];
  const gateways = gatewaysQuery.isError ? [] : gatewaysQuery.data ?? [];
  const cards = cardsQuery.isError ? undefined : cardsQuery.data;
  const defaultId = settingsQuery.data?.values.default_proxy_profile_id;
  const options = profiles.map((profile) => ({
    value: profile.id,
    label: proxyOptionLabel(profile, gateways, cards, defaultId),
  }));
  const resolveDefaultLabel = () => {
    if (settingsQuery.isError || profilesQuery.isError) return "默认网关读取失败";
    if (settingsQuery.isPending || profilesQuery.isPending) return "默认网关读取中";
    if (!defaultId) return "号池默认出站代理未配置";
    return options.find((option) => option.value === defaultId)?.label ?? "号池默认出站代理当前不可用";
  };
  const defaultLabel = resolveDefaultLabel();
  return { profilesQuery, profiles, options, defaultLabel };
};
