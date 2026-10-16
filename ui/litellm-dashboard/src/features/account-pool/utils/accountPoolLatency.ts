/** 将出口探测映射为卡片状态，不把未测、过期或其他节点结果显示为当前延迟。 */
import type { AccountPoolISPProbe, AccountPoolProxyGateway, AccountPoolProxyGatewayDelay } from "./AccountPoolTypes";

export function accountPoolLatency(
  gateway?: AccountPoolProxyGateway,
  measurement?: AccountPoolProxyGatewayDelay,
  probe?: AccountPoolISPProbe | null,
  now = Date.now(),
): { status: "ok" | "timeout" | "error" | "unknown" | "stale"; delay?: number; checkedAt?: string } {
  const result = (() => {
    if (gateway) {
      if (measurement?.port !== gateway.port || measurement.current_node !== gateway.current_node) return undefined;
      return { status: measurement.gateway_status, delay: measurement.gateway_delay_ms, checkedAt: measurement.checked_at };
    }
    if (probe) return { status: probe.status, delay: probe.delay_ms, checkedAt: probe.checked_at };
    return undefined;
  })();
  if (!result) return { status: "unknown" };
  if (!Number.isFinite(Date.parse(result.checkedAt)) || now - Date.parse(result.checkedAt) > 300_000) {
    return { status: "stale", checkedAt: result.checkedAt };
  }
  return { status: result.status === "ok" && result.delay == null ? "unknown" : result.status,
    delay: result.status === "ok" && result.delay != null ? result.delay : undefined, checkedAt: result.checkedAt };
}
