/** 为创建和授权页面复用代理目录、默认出口与完整卡片统计。 */
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { getAccountPoolSettings } from "../api/AccountPoolManagementApi";
import { proxyOptionLabel } from "../utils/AccountPoolProxyOptions";
import { accountPoolEnvironmentOptions, accountPoolProxyProfileOptions } from "./accountPoolOptions";
import { accountPoolQueryKeys } from "./accountPoolQueryKeys";
import { proxyGatewayQueryOptions } from "./useProxyGateways";

export const useAccountPoolProxyOptions = (accessToken: string | null, enabled: boolean) => {
  const { t } = useTranslation(undefined, { keyPrefix: "accountPool.proxyDisplay" });
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
    if (settingsQuery.isError || profilesQuery.isError) return t("failed");
    if (settingsQuery.isPending || profilesQuery.isPending) return t("loading");
    if (!defaultId) return t("notSet");
    return options.find((option) => option.value === defaultId)?.label ?? t("unavailable");
  };
  const defaultLabel = resolveDefaultLabel();
  return { profilesQuery, profiles, options, defaultLabel };
};
