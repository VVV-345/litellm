/** 验证代理展示按实际节点合并卡片，而不是仅按端口统计。 */
import { describe, expect, it } from "vitest";
import { proxyOptionLabel } from "./AccountPoolProxyOptions";

const gateways = [
  { port: 7891, profile_id: "p1", current_node: "日本02", isp_profile_id: null },
  { port: 7892, profile_id: "p2", current_node: "日本02", isp_profile_id: null },
  { port: 7893, profile_id: "p3", current_node: "美国01", isp_profile_id: null },
];
const cards = [{ proxy_profile_id: "p1" }, { proxy_profile_id: "p2" }, { proxy_profile_id: "p3" }];

describe("proxyOptionLabel", () => {
  it("shows explicit port, default marker and counts cards sharing a node across ports", () => {
    expect(proxyOptionLabel({ id: "p1", name: "主出口" }, gateways, cards, "p1"))
      .toBe("号池默认出站代理 · 主出口 · 端口 7891 · 节点：日本02 · 同节点 2 张卡片");
  });
  it("does not guess missing nodes or missing card statistics", () => {
    expect(proxyOptionLabel({ id: "p1", name: "主出口" }, [{ ...gateways[0], current_node: null }], cards))
      .toContain("节点未知 · 同节点卡片数未知");
    expect(proxyOptionLabel({ id: "p1", name: "主出口" }, gateways, undefined))
      .toContain("同节点卡片数未知");
  });
  it("groups ISP gateways by identity, not the port-specific selector name", () => {
    const isps = gateways.map((gateway, index) => ({ ...gateway, current_node: `出口${index}`, isp_profile_id: "isp1" }));
    expect(proxyOptionLabel({ id: "p1", name: "主出口" }, isps, cards)).toContain("同节点 3 张卡片");
  });
  it("keeps custom proxy names without inventing a gateway node", () => {
    expect(proxyOptionLabel({ id: "custom", name: "Proxy US" }, gateways, cards)).toBe("Proxy US");
  });
});
