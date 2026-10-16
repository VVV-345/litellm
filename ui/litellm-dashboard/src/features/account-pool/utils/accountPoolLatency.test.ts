/** 验证出口探测展示不混用节点、过期结果或模型耗时。 */
import { describe, expect, it } from "vitest";
import { accountPoolLatency } from "./accountPoolLatency";
import type { AccountPoolProxyGateway, AccountPoolProxyGatewayDelay } from "./AccountPoolTypes";
const now = Date.parse("2026-10-17T08:00:00Z");
const gateway: AccountPoolProxyGateway = { port: 7891, profile_id: "clash-gateway-7891", name: "Exit", proxy_url: "http://proxy:7891", current_node: "node-a" };
const delay: AccountPoolProxyGatewayDelay = { port: 7891, current_node: "node-a", checked_at: new Date(now).toISOString(), status: "ok", delay_ms: 42, gateway_status: "ok", gateway_delay_ms: 73 };
describe("accountPoolLatency", () => {
  it("uses the full gateway route measurement rather than model or node-only latency", () => {
    expect(accountPoolLatency(gateway, delay, undefined, now)).toMatchObject({ status: "ok", delay: 73 });
  });
  it("never reuses a different node's latency", () => {
    expect(accountPoolLatency({ ...gateway, current_node: "node-b" }, delay, undefined, now).status).toBe("unknown");
  });
  it("distinguishes failures and expired probes from zero latency", () => {
    expect(accountPoolLatency(gateway, { ...delay, gateway_status: "timeout", gateway_delay_ms: null }, undefined, now).status).toBe("timeout");
    expect(accountPoolLatency(gateway, delay, undefined, now + 301000).status).toBe("stale");
    expect(accountPoolLatency(undefined, undefined, undefined, now).status).toBe("unknown");
  });
  it("uses a direct ISP probe only when no gateway route is selected", () => {
    const probe = { profile_id: "isp-a", checked_at: new Date(now).toISOString(), status: "ok" as const, delay_ms: 81 };
    expect(accountPoolLatency(undefined, undefined, probe, now).delay).toBe(81);
    expect(accountPoolLatency(gateway, undefined, probe, now).status).toBe("unknown");
  });
});
