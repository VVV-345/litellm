/** 统一代理端口、默认出口和同节点卡片数的展示，不处理代理凭据。 */
import type { AccountPoolEnvironment, AccountPoolProxyGateway, AccountPoolProxyProfile } from "./AccountPoolTypes";

type Gateway = Pick<AccountPoolProxyGateway, "port" | "profile_id" | "current_node" | "isp_profile_id">;
type Card = Pick<AccountPoolEnvironment, "proxy_profile_id">;

const nodeIdentity = (gateway: Gateway | undefined) => {
  if (!gateway?.current_node || ["DIRECT", "REJECT"].includes(gateway.current_node)) return null;
  return gateway.isp_profile_id ? `isp:${gateway.isp_profile_id}` : `node:${gateway.current_node}`;
};

export const proxyOptionLabel = (
  profile: Pick<AccountPoolProxyProfile, "id" | "name">,
  gateways: readonly Gateway[],
  cards: readonly Card[] | undefined,
  defaultProfileId?: string | null,
) => {
  const gateway = gateways.find((item) => item.profile_id === profile.id);
  const identity = nodeIdentity(gateway);
  const usage = identity && cards
    ? `同节点 ${cards.filter((card) => nodeIdentity(gateways.find((item) => item.profile_id === card.proxy_profile_id)) === identity).length} 张卡片`
    : "同节点卡片数未知";
  const fallback = profile.id.startsWith("clash-gateway-") ? ["节点未知", usage] : [];
  const details = gateway ? [
    ...(!profile.name.includes(String(gateway.port)) ? [`端口 ${gateway.port}`] : []),
    gateway.current_node ? `节点：${gateway.current_node}` : "节点未知", usage,
  ] : fallback;
  return [
    ...(profile.id === defaultProfileId ? ["号池默认出站代理"] : []),
    profile.name,
    ...details,
  ].join(" · ");
};
