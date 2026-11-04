/** 统一代理类型、默认出口与同节点卡片统计，不处理代理凭据。 */
import type { AccountPoolEnvironment, AccountPoolProxyGateway, AccountPoolProxyProfile } from "./AccountPoolTypes";

type Gateway = Pick<AccountPoolProxyGateway, "port" | "profile_id" | "current_node" | "isp_profile_id">;
type Card = Pick<AccountPoolEnvironment, "proxy_profile_id">;
type Profile = Pick<AccountPoolProxyProfile, "id" | "name">;

const directISP = (id: string) => id.startsWith("isp-direct-");

export const proxyProfileLabel = (profile: Profile) =>
  `${directISP(profile.id) ? "ISP 出站代理" : "出站代理"} · ${profile.name}`;

const nodeIdentity = (id: string | null | undefined, gateways: readonly Gateway[]) => {
  if (id && directISP(id)) return `isp:${id.slice("isp-direct-".length)}`;
  const gateway = gateways.find((item) => item.profile_id === id);
  if (!gateway?.current_node || ["DIRECT", "REJECT"].includes(gateway.current_node)) return null;
  return gateway.isp_profile_id ? `isp:${gateway.isp_profile_id}` : `node:${gateway.current_node}`;
};

export const proxyOptionLabel = (
  profile: Profile,
  gateways: readonly Gateway[],
  cards: readonly Card[] | undefined,
  defaultProfileId?: string | null,
) => {
  const gateway = gateways.find((item) => item.profile_id === profile.id);
  const identity = nodeIdentity(profile.id, gateways);
  const usage = identity && cards
    ? `同节点 ${cards.filter((card) => nodeIdentity(card.proxy_profile_id, gateways) === identity).length} 张卡片`
    : "同节点卡片数未知";
  const gatewayFallback = profile.id.startsWith("clash-gateway-") ? ["节点未知", usage] : [];
  const fallback = directISP(profile.id) ? [usage] : gatewayFallback;
  const details = gateway ? [
    ...(!profile.name.includes(String(gateway.port)) ? [`端口 ${gateway.port}`] : []),
    gateway.current_node ? `节点：${gateway.current_node}` : "节点未知", usage,
  ] : fallback;
  return [
    ...(profile.id === defaultProfileId ? ["号池默认出站代理"] : []),
    proxyProfileLabel(profile),
    ...details,
  ].join(" · ");
};
