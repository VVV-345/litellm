// 启动独立 Camoufox；认证代理经本机转接，代理失败时不回退直连。
import { randomBytes } from "node:crypto";
import { mkdirSync } from "node:fs";
import { firefox } from "playwright-core";
import { Server } from "proxy-chain";
import { HelperError, upstreamUrl } from "./core.mjs";

export async function startBridge(proxy) {
  const password = randomBytes(32).toString("hex");
  const upstream = upstreamUrl(proxy);
  const server = new Server({
    host: "127.0.0.1",
    port: 0,
    verbose: false,
    prepareRequestFunction: ({ username, password: supplied }) => ({
      requestAuthentication: username !== "browser" || supplied !== password,
      upstreamProxyUrl: upstream,
      failMsg: "Proxy authentication required",
    }),
  });
  // 第三方异常可能含有代理 URL，禁止记录原始事件。
  server.on("requestFailed", () => {});
  await server.listen();
  return {
    proxy: {
      server: `http://127.0.0.1:${server.port}`,
      username: "browser",
      password,
      bypass: "localhost,127.0.0.1,[::1]",
    },
    close: () => server.close(true),
  };
}
export function browserLauncher(executablePath) {
  return async (directory, proxy) => {
    mkdirSync(directory, { recursive: true, mode: 0o700 });
    const bridge = await startBridge(proxy);
    try {
      const context = await firefox.launchPersistentContext(directory, {
        executablePath,
        headless: false,
        proxy: bridge.proxy,
        timeout: 45_000,
        firefoxUserPrefs: {
          "media.peerconnection.enabled": false,
          "network.proxy.failover_direct": false,
          "network.trr.mode": 5,
          "network.dns.disablePrefetch": true,
          "network.prefetch-next": false,
          "network.captive-portal-service.enabled": false,
          "network.connectivity-service.enabled": false,
        },
      });
      context.on("close", () => {
        void bridge.close().catch(() => {});
      });
      return context;
    } catch {
      await bridge.close();
      throw new HelperError("launch");
    }
  };
}
